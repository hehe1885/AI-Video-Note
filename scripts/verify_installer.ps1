param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [string]$TestRootBase = ''
)
$ErrorActionPreference = 'Stop'
if (-not $TestRootBase) {
    if ($env:CI -ne 'true' -or -not $env:RUNNER_TEMP) { throw 'Use a clean CI Windows runner, or explicitly pass an isolated -TestRootBase' }
    $TestRootBase = $env:RUNNER_TEMP
}
if (-not [IO.Path]::IsPathRooted($TestRootBase) -or -not (Test-Path -LiteralPath $TestRootBase -PathType Container)) { throw 'TestRootBase must be an existing absolute directory' }
$registration = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{E2B0188A-9456-48D6-82B4-21BB2D20FB80}_is1'
foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
    $key = [Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,$view)
    try { $existing = $key.OpenSubKey($registration); if ($existing) { $existing.Dispose(); throw 'Existing installation detected; refusing to overwrite it' } }
    finally { $key.Dispose() }
}
$runnerRoot = [IO.Path]::GetFullPath($TestRootBase).TrimEnd('\')
if ($runnerRoot -eq [IO.Path]::GetPathRoot($runnerRoot).TrimEnd('\')) { throw 'TestRootBase must not be a drive root' }
$testRoot = [IO.Path]::GetFullPath((Join-Path $runnerRoot ('ai-video-note-lifecycle-' + [Guid]::NewGuid().ToString('N'))))
if (-not $testRoot.StartsWith($runnerRoot+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid verification directory' }
$installerPath = (Resolve-Path -LiteralPath $Installer).Path
function Run-Installer {
    $process = Start-Process -FilePath $installerPath -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CURRENTUSER',('/DIR="'+$testRoot+'"')) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(180000) -or $process.ExitCode -ne 0) { throw 'Installer did not complete successfully' }
}
Run-Installer
$workspace = Join-Path $testRoot 'workspace'
$app = Join-Path $testRoot 'AI-Video-Note.exe'
$portableName = [IO.Path]::GetFileName($installerPath) -replace '-Setup-x64\.exe$', '-portable.exe'
$portable = Join-Path (Split-Path -Parent $installerPath) $portableName
if ($portableName -ne [IO.Path]::GetFileName($installerPath) -and (Test-Path -LiteralPath $portable)) {
    if ((Get-FileHash -LiteralPath $app).Hash -ne (Get-FileHash -LiteralPath $portable).Hash) { throw 'Installed executable does not match the audited portable build' }
    Write-Output 'Verified: installed executable matches the audited application SHA256.'
}
if (Test-Path -LiteralPath $workspace) { throw 'Fresh installer contains a user workspace' }
$allowedFiles = @('AI-Video-Note.exe','LICENSE','README.md','README.en.md','uninstall_helper.ps1','install-owner.json','unins000.exe','unins000.dat','unins000.msg')
foreach ($file in Get-ChildItem -LiteralPath $testRoot -Force) {
    if ($file.PSIsContainer -or $file.Name -notin $allowedFiles) { throw ('Unexpected fresh-install content: '+$file.Name) }
}
function Start-TestApplication {
    # Explicitly clear developer overrides for this child process only.
    $startInfo = New-Object Diagnostics.ProcessStartInfo
    $startInfo.FileName = $app
    $startInfo.Arguments = '--no-browser'
    $startInfo.WorkingDirectory = $testRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    foreach ($name in @('AI_VIDEO_NOTE_WORKSPACE','WHISPER_CACHE_DIR','HOST','PORT')) { $startInfo.EnvironmentVariables.Remove($name) }
    $process = [Diagnostics.Process]::Start($startInfo)
    $process.Dispose()
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        foreach ($port in 8765..8784) {
            try {
                $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 1
                if ($health.video_download_default_directory -eq (Join-Path $workspace 'video')) { return "http://127.0.0.1:$port" }
            } catch {}
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Installed app never became healthy'
}
$url = Start-TestApplication
$credentials = Invoke-RestMethod ($url+'/api/bili-credentials')
$keys = Invoke-RestMethod ($url+'/api/llm-keys')
$preferences = Invoke-RestMethod ($url+'/api/preferences')
$history = Invoke-RestMethod ($url+'/api/tasks')
if ($credentials.saved -ne $false -or @($keys.entries).Count -ne 0 -or $preferences.exists -ne $false -or $history.total -ne 0) { throw 'Fresh app did not start with factory defaults' }
foreach ($name in @('bili_credentials.json','llm_keys.json','preferences.json','.profile_entropy.bin','_welcome.json','_bili_profile','_douyin_profile')) {
    if (Test-Path -LiteralPath (Join-Path $workspace $name)) { throw ('Unexpected personal state in fresh app: '+$name) }
}
. (Join-Path $testRoot 'uninstall_helper.ps1') -LibraryOnly
Stop-InstallationProcesses $testRoot
Write-Output 'Verified: fresh installation has no saved Bilibili login, API keys, preferences or task history.'
New-Item -ItemType Directory -Force -Path "$workspace\notes","$workspace\_model_cache" | Out-Null
$fixtures = @{'preferences.json'='{"test":"preserve during update"}';'bili_credentials.json'='{"test":"fictional credentials"}';'llm_keys.json'='{}';'notes\keep.md'='Fixture note';'_model_cache\model.bin'='Fixture model'}
$hashes = @{}
foreach ($entry in $fixtures.GetEnumerator()) {
    $path = Join-Path $workspace $entry.Key
    [IO.File]::WriteAllText($path,$entry.Value)
    $hashes[$path] = (Get-FileHash -LiteralPath $path).Hash
}
$localData = Join-Path $testRoot 'custom-local-data'
New-Item -ItemType Directory -Path $localData | Out-Null
foreach ($name in @('note.md','model.bin')) {
    $path = Join-Path $localData $name
    [IO.File]::WriteAllText($path,'Fictional custom local data')
    $hashes[$path] = (Get-FileHash -LiteralPath $path).Hash
}
# An overwrite update must not run the data-deleting uninstall path.
Run-Installer
foreach ($path in $hashes.Keys) {
    if ((Get-FileHash -LiteralPath $path).Hash -ne $hashes[$path]) { throw "Update changed personal data: $path" }
}
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$startupName = 'AI-Video-Note-CI-Lifecycle'
$startupValue = '"' + $app + '" --no-browser'
if (-not (Test-Path -LiteralPath $runKey)) { New-Item -Path $runKey | Out-Null }
if ((Get-ItemProperty -LiteralPath $runKey).PSObject.Properties.Name -contains $startupName) { throw 'Existing lifecycle-test startup entry detected; refusing to overwrite it' }
Set-ItemProperty -LiteralPath $runKey -Name $startupName -Value $startupValue
$url = Start-TestApplication
$installedProcesses = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $app })
if ($installedProcesses.Count -lt 2) { throw 'Verification must include the running one-file launcher and background process' }
$uninstallers = @(Get-ChildItem -LiteralPath $testRoot -Filter 'unins*.exe')
if ($uninstallers.Count -ne 1) { throw 'Expected exactly one uninstaller' }
$uninstaller = $uninstallers[0]
$uninstall = Start-Process -FilePath $uninstaller.FullName -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+(Join-Path $runnerRoot 'ai-video-note-uninstall.log')+'"')) -WindowStyle Hidden -PassThru
if (-not $uninstall.WaitForExit(120000) -or $uninstall.ExitCode -ne 0) { throw 'Uninstaller did not complete successfully' }
Start-Sleep -Seconds 2
if (@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $app }).Count) { throw 'Uninstall left a background process running' }
if (Test-Path -LiteralPath $testRoot) { throw "Uninstall left its directory behind: $testRoot" }
if ((Get-ItemProperty -LiteralPath $runKey).PSObject.Properties.Name -contains $startupName) { throw 'Uninstall left its startup entry behind' }
try { $health = Invoke-RestMethod ($url+'/api/health') -TimeoutSec 1 } catch { $health = $null }
if ($health -and $health.video_download_default_directory -eq (Join-Path $workspace 'video')) { throw 'Uninstalled backend still responds' }
Write-Output 'Verified: update preserves settings, credentials, notes and models; uninstall exits background processes and removes installation data, directory and startup entry.'
