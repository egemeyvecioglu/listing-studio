@echo off
setlocal
cd /d "%~dp0"
echo Listing Studio - one-time setup
echo This creates the local app environment and trusts its localhost HTTPS certificate
echo for your Windows user. It does not install a general-purpose certificate authority.
echo Python 3.10 or newer must already be installed by the person setting up this PC.
echo.
py -3 --version >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python from python.org, then run this setup again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" app.py --make-certificate
if errorlevel 1 goto failed
certutil -user -addstore Root "%LOCALAPPDATA%\EtsyListingStudio\localhost.crt"
if errorlevel 1 goto failed
echo.
echo Setup complete. Open Start Listing Studio.cmd for everyday use.
pause
exit /b 0
:failed
echo Setup did not finish. Keep this window open for the person setting up the PC.
pause
exit /b 1
