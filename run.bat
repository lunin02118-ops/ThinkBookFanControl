@echo off
setlocal enabledelayedexpansion
title ThinkBook Fan Control Launcher

cd /d "%~dp0"

:: Check for Administrator privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Requesting Administrator privileges (required for ACPI/WMI hardware access)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Check for compiled executable
if exist "dist\ThinkBookFanControl\ThinkBookFanControl.exe" (
    start "" "dist\ThinkBookFanControl\ThinkBookFanControl.exe"
    exit /b 0
)

:: Try finding pythonw / python in PATH
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0main.py"
    exit /b 0
)

where python >nul 2>&1
if %errorlevel% equ 0 (
    start "" python "%~dp0main.py"
    exit /b 0
)

:: Fallback to standard Python 3.12 location
if exist "C:\Program Files\Python312\pythonw.exe" (
    start "" "C:\Program Files\Python312\pythonw.exe" "%~dp0main.py"
    exit /b 0
)

echo [ERROR] Python or compiled executable not found!
echo Please install Python 3.10+ or run Install_or_Run.bat
pause
exit /b 1
