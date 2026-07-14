@echo off
REM Windows launcher: auto-create venv and install deps on first run.
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

REM Check venv (avoid false positive if .venv was copied from Mac/Linux)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual env and installing dependencies ^(first run only^)...
  where py >nul 2>nul && ( py -m venv .venv ) || ( python -m venv .venv )
  if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create virtual env. Make sure Python 3.9+ is installed and in PATH.
    pause
    exit /b 1
  )
  ".venv\Scripts\python" -m pip install --upgrade pip
  ".venv\Scripts\python" -m pip install -r requirements.txt
)

".venv\Scripts\python" app.py
pause
