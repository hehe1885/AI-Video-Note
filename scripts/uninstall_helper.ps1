param(
    [ValidateSet('Prepare','Verify')][string]$Action = 'Prepare',
    [string]$InstallDirectory = '',
    [string]$LogPath = '',
    [switch]$LibraryOnly
)
$ErrorActionPreference = 'Stop'

function Get-InstallationRoot([string]$Directory, [bool]$RequireOwner = $true) {
    if (-not [IO.Path]::IsPathRooted($Directory)) { throw 'Installation path must be absolute' }
    $root = [IO.Path]::GetFullPath($Directory).TrimEnd('\')
    if ($root.StartsWith('\\?\')) { $root = $root.Substring(4) }
    $forbidden = @([IO.Path]::GetPathRoot($root).TrimEnd('\'), $env:USERPROFILE, $env:WINDIR,
        $env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})
    if ($forbidden -contains $root) { throw 'Refusing to clean a system or profile root' }
    $cursor = $root
    while ($cursor) {
        if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Installation path must not traverse a junction or symbolic link'
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    if (-not $RequireOwner) { return $root }
    $marker = Join-Path $root 'install-owner.json'
    $item = Get-Item -LiteralPath $marker -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Invalid installation marker' }
    $owner = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if ($owner.app_id -ne 'E2B0188A-9456-48D6-82B4-21BB2D20FB80' -or $owner.application -ne 'AI Video Note') {
        throw 'Installation ownership check failed'
    }
    return $root
}

function Get-InstallationProcessTree($Snapshot, [string]$Executable) {
    $selected = @{}
    foreach ($record in $Snapshot) {
        if ($record.ExecutablePath -and [string]::Equals($record.ExecutablePath, $Executable, [StringComparison]::OrdinalIgnoreCase)) {
            $selected[[int]$record.ProcessId] = $record
        }
    }
    do {
        $added = $false
        foreach ($record in $Snapshot) {
            $parent = $selected[[int]$record.ParentProcessId]
            if ($parent -and -not $selected.ContainsKey([int]$record.ProcessId) -and
                $record.CreationDate -ge $parent.CreationDate -and $record.Name -notmatch '^unins\d+\.exe$') {
                $selected[[int]$record.ProcessId] = $record
                $added = $true
            }
        }
    } while ($added)
    return @($selected.Values)
}

function Get-ProcessSnapshot { return @(Get-CimInstance Win32_Process -ErrorAction Stop) }

function Get-LiveTrackedProcess($Record) {
    $current = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$Record.ProcessId) -ErrorAction Stop
    if ($current -and $current.CreationDate -eq $Record.CreationDate) { return $current }
    return $null  # Never act on a reused PID.
}

function Stop-TrackedProcess($Record) {
    if (-not (Get-LiveTrackedProcess $Record)) { return }
    if ([int]$Record.ProcessId -eq $PID) { throw 'Refusing to terminate the uninstaller helper' }
    $process = $null
    try {
        $process = [Diagnostics.Process]::GetProcessById([int]$Record.ProcessId)
        $null = $process.Handle  # Keep a stable OS handle across the final identity check.
        if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $Record.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) { return }
        if (-not $process.HasExited) { $process.Kill() }
        if (-not $process.WaitForExit(10000)) { throw 'An installation process did not exit' }
    } catch {
        # A bootloader may exit on its own as its child stops.
        if (Get-LiveTrackedProcess $Record) { throw }
    } finally { if ($process) { $process.Dispose() } }
}

function Request-InstallationShutdown([string]$Root) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Root.ToLowerInvariant())) | ForEach-Object { $_.ToString('x2') }) -join '' }
    finally { $sha.Dispose() }
    $signal = $null
    try {
        $signal = [Threading.EventWaitHandle]::OpenExisting('Local\AI-Video-Note-Shutdown-' + $digest)
        $null = $signal.Set()
        Write-Output 'Requested graceful shutdown of this installation'
    } catch [Threading.WaitHandleCannotBeOpenedException] {
        Write-Output 'No shutdown listener; older versions will use verified process termination'
    } finally { if ($signal) { $signal.Dispose() } }
}

function Stop-InstallationProcesses([string]$Root) {
    $executable = Join-Path $Root 'AI-Video-Note.exe'
    $tracked = @{}
    function Remember($Records) {
        foreach ($record in $Records) {
            $key = [string]$record.ProcessId + '|' + $record.CreationDate.ToString('o')
            $tracked[$key] = $record
        }
    }
    Remember (Get-InstallationProcessTree (Get-ProcessSnapshot) $executable)
    if (-not $tracked.Count) { return }
    Request-InstallationShutdown $Root
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        Remember (Get-InstallationProcessTree (Get-ProcessSnapshot) $executable)
        $remaining = @($tracked.Values | Where-Object { Get-LiveTrackedProcess $_ })
        if (-not $remaining.Count) { return }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    # Includes workers observed before a launcher exits, even if they become orphans.
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        Remember (Get-InstallationProcessTree (Get-ProcessSnapshot) $executable)
        foreach ($record in @($tracked.Values | Sort-Object CreationDate -Descending)) { Stop-TrackedProcess $record }
        $remaining = @($tracked.Values | Where-Object { Get-LiveTrackedProcess $_ })
        $roots = @(Get-InstallationProcessTree (Get-ProcessSnapshot) $executable)
        if (-not $remaining.Count -and -not $roots.Count) { return }
    }
    throw 'Background processes are still running; uninstall has been stopped'
}

function Assert-NoReparseTree([string]$Path) {
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($Path)
    while ($pending.Count) {
        $current = Get-Item -LiteralPath $pending.Pop() -Force
        if ($current.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Data contains a junction or symbolic link; external targets were not removed'
        }
        if ($current.PSIsContainer) {
            foreach ($child in @(Get-ChildItem -LiteralPath $current.FullName -Force)) { $pending.Push($child.FullName) }
        }
    }
}

function Remove-InstallationData([string]$Directory) {
    $root = Get-InstallationRoot $Directory
    # Preserve the registered program files for Inno Setup to remove. All other
    # content inside this owned installation is local data, including custom
    # note/model folders placed outside workspace. Never traverse external links.
    $programFiles = @('AI-Video-Note.exe','LICENSE','README.md','README.en.md','uninstall_helper.ps1','install-owner.json')
    $targets = @()
    foreach ($item in @(Get-ChildItem -LiteralPath $root -Force)) {
        if (-not $item.PSIsContainer -and ($item.Name -in $programFiles -or $item.Name -match '^unins\d+\.(exe|dat|msg)$')) { continue }
        $target = [IO.Path]::GetFullPath($item.FullName)
        if (-not $target.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Data path escaped installation' }
        Assert-NoReparseTree $target
        $targets += $target
    }
    foreach ($target in $targets) {
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction Stop
        if (Test-Path -LiteralPath $target) { throw 'Installation data could not be removed' }
    }
}

function Assert-Uninstalled([string]$Directory) {
    $root = Get-InstallationRoot $Directory $false
    if (@(Get-InstallationProcessTree (Get-ProcessSnapshot) (Join-Path $root 'AI-Video-Note.exe')).Count) {
        throw 'An installation process is still running'
    }
    if (-not (Test-Path -LiteralPath $root)) { return }
    # The uninstaller removes its own files as it exits. Every other residual is an error.
    $leftovers = @(Get-ChildItem -LiteralPath $root -Force | Where-Object {
        $_.Name -notmatch '^unins\d+\.(exe|dat|msg)$'
    })
    if ($leftovers.Count) { throw ('Uninstall left files behind: ' + (($leftovers | Select-Object -ExpandProperty Name) -join ', ')) }
}

if (-not $LibraryOnly) {
    try {
        if ($LogPath) { Add-Content -LiteralPath $LogPath -Value ("Starting uninstall stage: $Action") -Encoding UTF8 }
        if ($Action -eq 'Prepare') {
            $root = Get-InstallationRoot $InstallDirectory
            Stop-InstallationProcesses $root
            Remove-InstallationData $root
        } else { Assert-Uninstalled $InstallDirectory }
        if ($LogPath) { Add-Content -LiteralPath $LogPath -Value ("Completed uninstall stage: $Action") -Encoding UTF8 }
    } catch {
        if ($LogPath) { Add-Content -LiteralPath $LogPath -Value $_.Exception.Message -Encoding UTF8 }
        Write-Error $_; exit 1
    }
}
