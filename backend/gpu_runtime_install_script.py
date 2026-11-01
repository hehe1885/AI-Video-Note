"""Fixed-path elevated installer. Release hashes are embedded, never supplied by HTTP."""

SCRIPT = r'''
param(
    [Parameter(Mandatory=$true)][string]$CacheDirectory,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-f0-9]{32}$')][string]$JobId,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
$packages = @'
__PACKAGES__
'@ | ConvertFrom-Json
$files = @'
__FILES__
'@ | ConvertFrom-Json
$programRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
$cacheRoot = [IO.Path]::GetFullPath($CacheDirectory)
$resultPath = Join-Path $cacheRoot ("status-$JobId.json")
$opened = @{}
$created = [System.Collections.Generic.List[object]]::new()
$mutex = $null
$ownsMutex = $false
function Write-Status($value) {
    $temporary = Join-Path $cacheRoot ([Guid]::NewGuid().ToString('N') + '.json.tmp')
    try {
        [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Depth 5), [Text.Encoding]::UTF8)
        Move-Item -LiteralPath $temporary -Destination $resultPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
}
try {
    if (-not $ValidateOnly) {
        $mutex = [Threading.Mutex]::new($false, 'Local\AI-Video-Note-NVIDIA-Runtime-Install')
        try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) { throw 'Another GPU runtime installation is running. Wait and retry.' }
    }
    foreach ($package in $packages) {
        # Keep the verified file open with write/delete sharing disabled until extraction ends.
        $stream = [IO.File]::Open((Join-Path $cacheRoot $package.filename), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $hash = [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() } finally { $sha.Dispose() }
            if ($hash -ne $package.sha256) { throw "Archive checksum mismatch: $($package.filename)" }
            $stream.Position = 0
            $opened[$package.component] = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Read, $false)
        } catch { $stream.Dispose(); throw }
    }
    foreach ($file in $files) {
        $file | Add-Member -NotePropertyName target -NotePropertyValue ([IO.Path]::GetFullPath((Join-Path $programRoot $file.destination_relative)))
        $allowed = @((Join-Path $programRoot 'NVIDIA GPU Computing Toolkit\CUDA\v12.9\'), (Join-Path $programRoot 'NVIDIA\CUDNN\v9.27\'))
        if (-not ($allowed | Where-Object { $file.target.StartsWith([IO.Path]::GetFullPath($_), [StringComparison]::OrdinalIgnoreCase) })) { throw 'Unexpected target path' }
        if (-not $opened[$file.component].GetEntry($file.entry)) { throw "Missing package entry: $($file.entry)" }
        if (-not $ValidateOnly -and (Test-Path -LiteralPath $file.target) -and (Get-FileHash -LiteralPath $file.target -Algorithm SHA256).Hash -ne $file.sha256) {
            throw "An existing different runtime file will not be replaced: $($file.target). Use the official installer to repair it."
        }
    }
    if ($ValidateOnly) { Write-Status @{status='validated';total=$files.Count}; exit 0 }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator permission is required.' }
    Write-Status @{status='installing';done=0;total=$files.Count}
    $done = 0
    foreach ($file in $files) {
        if (-not (Test-Path -LiteralPath $file.target)) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $file.target) -Force | Out-Null
            $temporary = $file.target + '.' + [Guid]::NewGuid().ToString('N') + '.tmp'
            try {
                $entry = $opened[$file.component].GetEntry($file.entry).Open()
                try {
                    $output = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                    try { $entry.CopyTo($output) } finally { $output.Dispose() }
                } finally { $entry.Dispose() }
                if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $file.sha256) { throw 'Extracted file checksum mismatch' }
                Move-Item -LiteralPath $temporary -Destination $file.target
                $created.Add($file)
            } finally {
                if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
            }
        }
        $done++
        Write-Status @{status='installing';done=$done;total=$files.Count}
    }
    foreach ($file in $files) {
        if ((Get-FileHash -LiteralPath $file.target -Algorithm SHA256).Hash -ne $file.sha256) { throw 'Installed file checksum mismatch' }
    }
    Write-Status @{status='completed';done=$files.Count;total=$files.Count}
    exit 0
} catch {
    $reason = $_.Exception.Message
    foreach ($file in $created) {
        try {
            if ((Test-Path -LiteralPath $file.target) -and (Get-FileHash -LiteralPath $file.target -Algorithm SHA256).Hash -eq $file.sha256) {
                Remove-Item -LiteralPath $file.target -Force
            }
        } catch { $reason += ' Rollback could not remove a file that may be in use.' }
    }
    Write-Status @{status='failed';error=$reason}
    exit 1
} finally {
    foreach ($archive in $opened.Values) { $archive.Dispose() }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
}
'''
