@echo off
chcp 65001 >nul
title ThinkBook Fan Control — Hardware Verification & Launcher (Admin)

cd /d "%~dp0"

:: Request Administrator privileges via standard UAC if not elevated
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Запрос прав Администратора (UAC) для доступа к Lenovo EC WMI...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs"
    exit /b 0
)

echo ======================================================================
echo   Lenovo ThinkBook 14 G6+ IMH — Проверка оборудования и запуск кулеров
echo ======================================================================
echo.

echo [1/3] Запуск полного набора модульных, симуляционных и live WMI/NVML тестов...
python "%~dp0test_suite.py" -v
if %errorlevel% neq 0 (
    echo [ERROR] Ошибка в test_suite.py!
    pause
    exit /b 1
)
python "%~dp0test_controller_simulation.py" -v
if %errorlevel% neq 0 (
    echo [ERROR] Ошибка в test_controller_simulation.py!
    pause
    exit /b 1
)

echo.
echo [2/3] Регистрация автозапуска через Планировщик заданий (без UAC-блокировки)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
    "$exe = 'C:\tools\ThinkBookFanControl\dist\ThinkBookFanControl\ThinkBookFanControl.exe'; " ^
    "$wd  = 'C:\tools\ThinkBookFanControl\dist\ThinkBookFanControl'; " ^
    "$act = New-ScheduledTaskAction -Execute $exe -Argument '--minimized' -WorkingDirectory $wd; " ^
    "$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; " ^
    "$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew; " ^
    "$prn = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest; " ^
    "Register-ScheduledTask -TaskName 'ThinkBookFanControl' -Action $act -Trigger $trg -Settings $set -Principal $prn -Force | Out-Null; " ^
    "Remove-ItemProperty -LiteralPath 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -Name 'ThinkBookFanControl' -ErrorAction SilentlyContinue; " ^
    "Write-Host '[OK] Автозапуск Планировщика заданий настроен (работает от сети и от батареи, без лимита 72ч).' -ForegroundColor Green"

echo.
echo [3/3] Запуск скомпилированного приложения ThinkBookFanControl.exe...
start "" /D "C:\tools\ThinkBookFanControl\dist\ThinkBookFanControl" "C:\tools\ThinkBookFanControl\dist\ThinkBookFanControl\ThinkBookFanControl.exe"
echo [OK] ThinkBookFanControl запущен! Окно и иконка в системном трее активны.
echo.
timeout /t 4
exit /b 0
