@echo off
setlocal enabledelayedexpansion
title ThinkBook Fan Control - Setup & Launcher

cd /d "%~dp0"

echo =====================================================================
echo       Lenovo ThinkBook 14 G6+ Fan Control - Setup & Launcher
echo =====================================================================
echo.

:: 1. Check Administrator Privileges
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Requesting Administrator privileges for hardware access...
    powershell -Command "Start-Process cmd -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

:: 2. Check Python installation
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not found in your system PATH!
    echo Please install Python 3.10+ from https://python.org and tick "Add to PATH".
    echo Alternatively, download the pre-compiled portable release from GitHub Releases.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('python --version') do set PY_VER=%%v
echo [OK] Detected %PY_VER%
echo.

:: 3. Install / Verify Dependencies
echo [INFO] Installing / verifying required packages from requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARNING] Encountered issues during package installation.
    echo Continuing anyway...
)
echo.

:: 4. User Action Selection
echo Choose an action:
echo  [1] Launch ThinkBook Fan Control now (Default)
echo  [2] Build standalone executable (PyInstaller)
echo  [3] Run Automated Test Suite
echo  [4] Exit
echo.
set /p USER_CHOICE="Enter your choice (1-4, default is 1): "

if "%USER_CHOICE%"=="2" goto BUILD_EXE
if "%USER_CHOICE%"=="3" goto RUN_TESTS
if "%USER_CHOICE%"=="4" exit /b 0

:RUN_APP
echo [INFO] Starting ThinkBook Fan Control...
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw "%~dp0main.py"
) else (
    start "" python "%~dp0main.py"
)
exit /b 0

:BUILD_EXE
echo.
echo [INFO] Building standalone executable using PyInstaller...
python -m PyInstaller --noconfirm ThinkBookFanControl.spec
if %errorlevel% equ 0 (
    echo.
    echo [SUCCESS] Build completed!
    echo Executable is located at: %~dp0dist\ThinkBookFanControl\ThinkBookFanControl.exe
) else (
    echo.
    echo [ERROR] PyInstaller build failed. Check error log above.
)
pause
exit /b 0

:RUN_TESTS
echo.
echo [INFO] Running test suite...
python test_suite.py
python test_controller_simulation.py
echo.
pause
exit /b 0
