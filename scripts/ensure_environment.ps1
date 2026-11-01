param([switch]$PrepareOnly, [switch]$Repair)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location -LiteralPath $projectRoot
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
function Test-ProjectPython([string]$candidate) {
    if (-not (Test-Path -LiteralPath $candidate)) { return $false }
    try {
        & $candidate -c 'import sys; assert (3,11) <= sys.version_info[:2] < (3,15); assert sys.maxsize > 2**32' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
if (-not (Test-ProjectPython $python)) {
    $systemPython = $null
    foreach ($name in @('py','python','python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $command) { continue }
        try {
            $arguments = @('-c','import sys; print(sys.executable)')
            if ($name -eq 'py') { $arguments = @('-3') + $arguments }
            $result = @(& $command.Source @arguments 2>$null)
            if ($LASTEXITCODE -eq 0 -and $result.Count -gt 0) {
                $candidate = [string]$result[-1]
                if (Test-ProjectPython $candidate) { $systemPython = $candidate; break }
            }
        } catch { }
    }
    if (-not $systemPython) {
        throw 'Please install 64-bit Python 3.11 to 3.14, then run start.bat again. https://www.python.org/downloads/'
    }
    $venvDir = Join-Path $projectRoot '.venv'
    if (Test-Path -LiteralPath $venvDir) {
        # Keep the old environment rather than deleting its contents.
        $backup = Join-Path $projectRoot ('.venv-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        Move-Item -LiteralPath $venvDir -Destination $backup
        Write-Host "Previous environment preserved at: $backup"
    }
    Write-Host 'Preparing the project Python environment...'
    & $systemPython -m venv $venvDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-ProjectPython $python)) { throw 'Failed to create the project environment.' }
}
$bootstrapArgs = @((Join-Path $projectRoot 'scripts\bootstrap.py'))
if ($Repair) { $bootstrapArgs += '--repair' }
& $python @bootstrapArgs
if ($LASTEXITCODE -ne 0) { throw 'Dependency preparation failed. Check the error above and retry start.bat.' }
if ($PrepareOnly) { Write-Output $python; return }
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
Start-Process -FilePath $pythonw -ArgumentList @(('"' + (Join-Path $projectRoot 'launcher.py') + '"'), '--tray') -WorkingDirectory $projectRoot -WindowStyle Hidden
exit 0
