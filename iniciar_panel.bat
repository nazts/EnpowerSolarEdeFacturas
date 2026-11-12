@echo off
chcp 65001 >nul
title Enpower Solar - panel de facturas
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Falta el entorno virtual. Corre primero:
    echo       powershell -ExecutionPolicy Bypass -File scripts\instalar_windows.ps1
    echo.
    if /i not "%~1"=="/tarea" pause
    exit /b 1
)

if not exist "logs" mkdir "logs"

rem puerto configurado en .env (por defecto 8000)
set PUERTO=8000
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"PORT=" .env 2^>nul') do set PUERTO=%%a
set PUERTO=%PUERTO: =%

rem si ya hay un panel escuchando en ese puerto, no arrancar otro
set OCUPADO=
for /f %%p in ('powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PUERTO% -State Listen -ErrorAction SilentlyContinue) { 'si' }"') do set OCUPADO=%%p
if /i "%OCUPADO%"=="si" (
    echo.
    echo   Ya hay un panel escuchando en el puerto %PUERTO%.
    echo   Usa detener_panel.bat si quieres reiniciarlo.
    echo.
    if /i not "%~1"=="/tarea" pause
    exit /b 0
)

if /i "%~1"=="/tarea" (
    rem lanzado por el Programador de tareas: sin ventana util, todo al log
    ".venv\Scripts\python.exe" run.py >> "logs\panel.log" 2>&1
) else (
    echo ==============================================================
    echo   Enpower Solar - panel de facturas
    echo ==============================================================
    ".venv\Scripts\python.exe" run.py
)
set CODIGO=%ERRORLEVEL%

if /i "%~1"=="/tarea" exit /b %CODIGO%

echo.
echo   El panel se detuvo (codigo %CODIGO%).
echo   Si hubo un error, revisa logs\panel.log
pause
exit /b %CODIGO%
