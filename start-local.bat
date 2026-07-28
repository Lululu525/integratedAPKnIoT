@echo off
setlocal

set "PORTAL_ROOT=%~dp0"
set "PLATFORM_ROOT=%~dp0..\apk-analysis-platform"
set "PYTHON_EXE=%PLATFORM_ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo ERROR: APK Analysis Platform virtual environment was not found.
  echo Run setup.bat in %PLATFORM_ROOT% first.
  pause
  exit /b 1
)

echo Starting Apionix portal on http://127.0.0.1:8080 ...
start "Apionix Portal" /D "%PORTAL_ROOT%" "%PYTHON_EXE%" -m http.server 8080 --bind 127.0.0.1

echo Starting APK frontend on http://127.0.0.1:5173 ...
start "APK Frontend" /D "%PLATFORM_ROOT%\FrontendUI\dist" "%PYTHON_EXE%" -m http.server 5173 --bind 127.0.0.1

echo Starting FastAPI on http://127.0.0.1:8000 ...
start "APK API" /D "%PLATFORM_ROOT%\apk-platform" cmd /k "set CELERY_TASK_ALWAYS_EAGER=1&& ""%PYTHON_EXE%"" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000"

echo.
echo All local services were started.
echo Open http://127.0.0.1:8080
endlocal
