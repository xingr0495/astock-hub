@echo off
setlocal
cd /d "%~dp0"
title astock-hub installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Start
if errorlevel 1 (
  echo.
  echo Installation failed. Review the message above.
  pause
  exit /b 1
)
echo.
echo Installation finished. The panel should open in your browser.
pause
