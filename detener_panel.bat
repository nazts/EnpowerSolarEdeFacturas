@echo off
chcp 65001 >nul
title Enpower Solar - detener panel
cd /d "%~dp0"

set PUERTO=8000
for /f "tokens=2 delims==" %%a in ('findstr /b /c:"PORT=" .env 2^>nul') do set PUERTO=%%a
set PUERTO=%PUERTO: =%

echo Deteniendo el panel de Enpower Solar (puerto %PUERTO%)...
powershell -NoProfile -Command ^
  "$procs = Get-NetTCPConnection -LocalPort %PUERTO% -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "if (-not $procs) { Write-Host '   no estaba corriendo (nadie escucha en ese puerto)' } " ^
  "else { foreach ($id in $procs) { $p = Get-Process -Id $id -ErrorAction SilentlyContinue; Write-Host ('   deteniendo ' + $p.ProcessName + ' (PID ' + $id + ')'); taskkill /PID $id /T /F | Out-Null } }"

echo Listo.
rem pausa sin depender de una consola interactiva
ping -n 4 127.0.0.1 >nul
