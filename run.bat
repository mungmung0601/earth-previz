@echo off
cd /d "%~dp0"

where python >nul 2>&1 || (
    echo ERROR: python not found. Install Python 3.9+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

python -c "import sys; exit(0 if sys.version_info >= (3,9) else 1)" 2>nul || (
    echo ERROR: Python 3.9+ required.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Installing dependencies (Flask, Playwright, FFmpeg, etc.)...
pip install -q -r requirements.txt
python -m playwright install chromium 2>nul

echo.
echo Starting Earth Previz...
echo Open http://127.0.0.1:5100 in your browser
echo.
python app.py
pause
