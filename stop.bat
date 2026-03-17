@echo off
:: ============================================================================
:: stop.bat — Windows launcher for stopping ai-dev-platform services
:: Double-click or run from any terminal.
:: Pass -StopInfra to also stop Redis + PostgreSQL Docker containers:
::   stop.bat -StopInfra
:: ============================================================================

echo.
echo  ai-dev-platform ^| Stopping services
echo.

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] PowerShell not found.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*

if errorlevel 1 (
    echo.
    echo  [ERROR] stop.ps1 reported a failure (exit code %errorlevel%).
    pause
    exit /b %errorlevel%
)

pause
