@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m app.capturar_historial >> logs\captura.log 2>&1
