@echo off
cd /d "%~dp0"
echo === One-time setup ===
where python >nul 2>nul || (echo Python is not installed. Install Python 3.11+ from python.org and tick "Add to PATH". & pause & exit /b 1)
if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env (copy .env.example .env >nul & echo Created .env  -  open it and fill in your Meta details.)
echo.
echo Setup finished. Now double-click start_dashboard.bat
pause
