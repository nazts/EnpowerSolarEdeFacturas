@echo off
chcp 65001 >nul
title Enpower Solar - arrancar WSL
REM Despierta la distro de WSL y deja corriendo los servicios del panel.
REM Lo lanza la tarea "EnpowerSolar WSL" o se usa a mano.
REM   iniciar_wsl.bat            usa la distro Ubuntu
REM   iniciar_wsl.bat Ubuntu-24.04
REM Si no se pasa distro se intenta leer del archivo .distro (lo escribe tareas_wsl.ps1).

cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set REGISTRO=logs\wsl_arranque.log

set DISTRO=%~1
if "%DISTRO%"=="" if exist ".distro" set /p DISTRO=<.distro
if "%DISTRO%"=="" set DISTRO=Ubuntu
set DISTRO=%DISTRO: =%

echo ==== %date% %time% distro=%DISTRO% ==== >> "%REGISTRO%"

where wsl.exe >nul 2>&1
if errorlevel 1 (
    echo    WSL no esta instalado: wsl --install -d Ubuntu >> "%REGISTRO%"
    echo    WSL no esta instalado. Corre: wsl --install -d Ubuntu
    exit /b 1
)

REM arranca el panel y el timer dentro de la distro
wsl.exe -d %DISTRO% -u root --exec /bin/systemctl start enpower-panel.service >> "%REGISTRO%" 2>&1
set RC1=%ERRORLEVEL%
wsl.exe -d %DISTRO% -u root --exec /bin/systemctl start enpower-runner.timer >> "%REGISTRO%" 2>&1
set RC2=%ERRORLEVEL%

REM WSL apaga la distro cuando no queda ningun proceso: se deja uno vivo
start "" /min wsl.exe -d %DISTRO% -u root --exec /bin/sleep infinity

echo    panel=%RC1% timer=%RC2% >> "%REGISTRO%"
if not "%RC1%"=="0" (
    echo    El panel no arranco (codigo %RC1%). Revisa: wsl -d %DISTRO% -- systemctl status enpower-panel
    exit /b %RC1%
)
echo    Panel de Enpower Solar arrancado en WSL (%DISTRO%)
exit /b %RC2%
