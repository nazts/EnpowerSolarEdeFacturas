@echo off
chcp 65001 >nul
title Enpower Solar - corrida de facturas
cd /d "%~dp0.."
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" (
    echo Falta el entorno virtual: powershell -ExecutionPolicy Bypass -File scripts\instalar_windows.ps1
    exit /b 1
)

if not exist "logs" mkdir "logs"

rem el formato de %date% cambia con el idioma de Windows: se lo preguntamos a PowerShell
set HOY=
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set HOY=%%i
if "%HOY%"=="" set HOY=sinfecha
set ARCHIVO=logs\runner_%HOY%.log

echo ==== corrida %date% %time% ==== >> "%ARCHIVO%"
".venv\Scripts\python.exe" -m app.runner %* >> "%ARCHIVO%" 2>&1
set RC=%ERRORLEVEL%
echo ==== fin %date% %time% (codigo %RC%) ==== >> "%ARCHIVO%"
exit /b %RC%
