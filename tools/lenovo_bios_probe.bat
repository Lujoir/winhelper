@echo off
rem EyeTerm Lenovo BIOS read-only probe launcher
rem Right-click -> Run as administrator
cd /d "%~dp0"
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [Info] Not elevated. Requesting admin...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lenovo_bios_probe.ps1"
pause
