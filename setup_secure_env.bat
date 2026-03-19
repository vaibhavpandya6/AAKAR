@echo off
REM Setup secure environment variables for a project (Windows)

setlocal enabledelayedexpansion

set PROJECT_ID=%1

if "%PROJECT_ID%"=="" (
    echo Usage: setup_secure_env.bat ^<project_id^>
    echo.
    echo Example: setup_secure_env.bat proj-123
    exit /b 1
)

set SECURE_ENV_DIR=secure_env
set ENV_FILE=%SECURE_ENV_DIR%\%PROJECT_ID%.env

REM Create secure_env directory if it doesn't exist
if not exist "%SECURE_ENV_DIR%" (
    echo Creating %SECURE_ENV_DIR% directory...
    mkdir "%SECURE_ENV_DIR%"
)

REM Check if .env file already exists
if exist "%ENV_FILE%" (
    echo WARNING: Environment file already exists: %ENV_FILE%
    set /p OVERWRITE="Overwrite? (y/N): "
    if /i not "!OVERWRITE!"=="y" (
        echo Aborted.
        exit /b 0
    )
)

REM Copy template or create minimal .env file
if exist "%SECURE_ENV_DIR%\.env.template" (
    copy "%SECURE_ENV_DIR%\.env.template" "%ENV_FILE%" >nul
    echo Created %ENV_FILE% from template
) else (
    (
        echo # Environment variables for generated code
        echo # Add your credentials here
        echo.
        echo DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
        echo API_KEY=your-api-key-here
    ) > "%ENV_FILE%"
    echo Created minimal %ENV_FILE%
)

echo.
echo Next steps:
echo    1. Edit %ENV_FILE% with your credentials
echo    2. Never commit this file to git
echo    3. Secure the file permissions in Windows Explorer
echo.
echo Security notes:
echo    - Right-click file ^> Properties ^> Security
echo    - Remove all users except yourself
echo    - This directory should be in .gitignore
