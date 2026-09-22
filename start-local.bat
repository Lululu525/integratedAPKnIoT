@echo off
setlocal
set "APK_PYTHON=%~dp0..\apk-analysis-platform\.venv\Scripts\python.exe"

if not exist "%APK_PYTHON%" (
  echo ERROR: APK Analysis Platform virtual environment was not found.
  pause
  exit /b 1
)

"%APK_PYTHON%" "%~dp0run_integrated.py"
if errorlevel 1 pause
endlocal
