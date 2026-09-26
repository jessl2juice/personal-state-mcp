@echo off
setlocal
title Personal State Setup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-PersonalState.ps1"
if errorlevel 1 (
  echo.
  echo Personal State setup did not finish. Review the message above.
  pause
  exit /b 1
)
echo.
echo Personal State setup is complete.
pause
