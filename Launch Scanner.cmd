@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Follow the one-time setup in README.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m windows_client.launcher
if errorlevel 1 pause
