@echo off
title WhatsApp Campaign Dashboard
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (call .venv\Scripts\activate.bat) else (echo Run setup_windows.bat first. & pause & exit /b 1)
echo ========================================================
echo  WhatsApp Campaign Dashboard  -  http://localhost:8000
echo  Close this window to stop it.
echo ========================================================
start "" http://localhost:8000
python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
