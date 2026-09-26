@echo off
setlocal
title Personal State Uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Uninstall-PersonalState.ps1"
if errorlevel 1 (
  echo.
  echo Personal State was not fully removed. Review the message above.
  pause
  exit /b 1
)
pause
