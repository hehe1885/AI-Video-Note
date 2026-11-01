param(
    [string]$Python = '',
    [string]$ISCC = '',
    [string]$ChineseMessages = '',
    [switch]$SkipBuild,
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$version = (Select-String -LiteralPath (Join-Path $projectRoot 'launcher.py') -Pattern '^VERSION = "([^"]+)"').Matches[0].Groups[1].Value
if (-not $version) { throw 'Unable to read application version' }
if (-not $ISCC) {
    $compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($compiler) { $ISCC = $compiler.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )) {
        if (-not $ISCC -and (Test-Path -LiteralPath $candidate)) { $ISCC = $candidate }
    }
}
if (-not $ISCC -or -not (Test-Path -LiteralPath $ISCC)) { throw 'Install Inno Setup 7 or 6, or pass -ISCC <path to ISCC.exe>.' }
if (-not $ChineseMessages) { $ChineseMessages = Join-Path (Split-Path -Parent $ISCC) 'Languages\ChineseSimplified.isl' }
if (-not (Test-Path -LiteralPath $ChineseMessages)) { throw 'ChineseSimplified.isl is required. Pass -ChineseMessages <path> if it is not installed with Inno Setup.' }
if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot 'build_exe.ps1') -Python $Python -SkipInstall:$SkipInstall
    if (-not $?) { throw 'Application build failed' }
}
$executable = Join-Path $projectRoot "dist\AI-Video-Note-$version-portable.exe"
if (-not (Test-Path -LiteralPath $executable)) { throw "Build the application first: $executable" }
& $ISCC "/DAppVersion=$version" "/DProjectRoot=$projectRoot" "/DAppExe=$executable" "/DChineseMessages=$ChineseMessages" (Join-Path $PSScriptRoot 'installer.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
$installer = Join-Path $projectRoot "dist\AI-Video-Note-$version-Setup-x64.exe"
if (-not (Test-Path -LiteralPath $installer)) { throw 'Compiler did not create the installer' }
$checksum = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText($installer + '.sha256', "$checksum  $([System.IO.Path]::GetFileName($installer))`n", [System.Text.Encoding]::ASCII)
Write-Output "Installer: $installer"
Write-Output "SHA256: $checksum"
