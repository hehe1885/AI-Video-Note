@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure_environment.ps1"
if errorlevel 1 (
    echo.
    echo 启动失败，请查看上方提示。检查网络后可再次双击本文件重试。
    pause
    exit /b 1
)
endlocal
