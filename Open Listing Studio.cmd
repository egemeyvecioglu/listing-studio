@echo off
setlocal
cd /d "%~dp0"
title Listing Studio
if not exist "runtime\python.exe" (
  echo Please extract the complete Listing Studio Windows ZIP first.
  echo This launcher needs the runtime folder included in that ZIP.
  pause
  exit /b 1
)
if not exist "%LOCALAPPDATA%\EtsyListingStudio\localhost-trusted.txt" (
  echo First-time setup: preparing the secure connection on this PC.
  echo Windows may ask you to trust this app's localhost certificate.
  echo It is used only for the app running on this computer.
  echo.
  "runtime\python.exe" app.py --make-certificate
  if errorlevel 1 goto failed
  certutil -user -addstore Root "%LOCALAPPDATA%\EtsyListingStudio\localhost.crt"
  if errorlevel 1 goto failed
  echo Local certificate added.>"%LOCALAPPDATA%\EtsyListingStudio\localhost-trusted.txt"
)
echo Opening Listing Studio in your browser...
echo Keep this window open while using the app. Close it to stop the app.
echo.
"runtime\python.exe" app.py --https
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Listing Studio could not start. Please share a screenshot of this window.
pause
exit /b 1
