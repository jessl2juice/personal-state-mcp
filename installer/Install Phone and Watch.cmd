@echo off
setlocal
title Personal State Phone and Watch Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AndroidCompanions.ps1"
if errorlevel 1 (
  echo.
  echo Companion setup did not finish. Review the message above.
  pause
  exit /b 1
)
echo.
echo Phone and watch setup is complete.
pause
