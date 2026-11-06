@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Setup Windows.cmd once before opening Listing Studio.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" app.py --https
if errorlevel 1 pause
