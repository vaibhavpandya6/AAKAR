@echo off
:: ============================================================================
:: start.bat — Windows launcher for ai-dev-platform
:: Double-click this file or run from Command Prompt / PowerShell terminal.
:: It bypasses the execution-policy restriction and delegates to start.ps1.
:: ============================================================================

echo.
echo  ai-dev-platform ^| Windows launcher
echo.

:: Check that PowerShell is available
where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] PowerShell not found. Install it from:
    echo          https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows
    pause
    exit /b 1
)

:: Run start.ps1 with Bypass execution policy so no system-wide policy change is needed.
:: -NoProfile skips user profile loading for a faster start.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*

if errorlevel 1 (
    echo.
    echo  [ERROR] start.ps1 reported a failure (exit code %errorlevel%).
    echo  Check the output above for details.
    pause
    exit /b %errorlevel%
)

pause
