@echo off
REM Quick service check script for Windows

echo Checking services...
echo.

REM Check Redis
echo Redis (6379):
powershell -Command "Test-NetConnection -ComputerName localhost -Port 6379 -InformationLevel Quiet" > nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Running
) else (
    echo   [X] Not running
    echo   Start with: docker run -d -p 6379:6379 redis:latest
)

echo.

REM Check PostgreSQL
echo PostgreSQL (5432):
powershell -Command "Test-NetConnection -ComputerName localhost -Port 5432 -InformationLevel Quiet" > nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Running
) else (
    echo   [X] Not running
    echo   Start with: docker run -d -p 5432:5432 -e POSTGRES_USER=user -e POSTGRES_PASSWORD=password -e POSTGRES_DB=aidevplatform postgres:15
)

echo.

REM Check API Server
echo API Server (8000):
powershell -Command "Test-NetConnection -ComputerName localhost -Port 8000 -InformationLevel Quiet" > nul 2>&1
if %errorlevel% equ 0 (
    echo   [OK] Running
) else (
    echo   [X] Not running
    echo   Start with: python start_server.py
)

echo.
echo Done!
pause
