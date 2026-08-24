@echo off
setlocal

set "PORTAL_ROOT=%~dp0"
set "PLATFORM_ROOT=%~dp0..\apk-analysis-platform"
set "APK_PYTHON=%PLATFORM_ROOT%\.venv\Scripts\python.exe"
set "IOT_ROOT=%~dp0..\ESP-Firmware-Over-The-Air"
set "IOT_PYTHON=%IOT_ROOT%\.venv\Scripts\python.exe"

if not exist "%APK_PYTHON%" (
  echo ERROR: APK Analysis Platform virtual environment was not found.
  echo Run setup.bat in %PLATFORM_ROOT% first.
  pause
  exit /b 1
)

if not exist "%IOT_PYTHON%" (
  echo ERROR: IoT Platform virtual environment was not found.
  echo Expected: %IOT_PYTHON%
  pause
  exit /b 1
)

if not exist "%IOT_ROOT%\frontend\node_modules" (
  echo ERROR: IoT frontend dependencies were not found.
  echo Run npm install in %IOT_ROOT%\frontend first.
  pause
  exit /b 1
)

echo Starting Apionix portal on http://127.0.0.1:8080 ...
start "Apionix Portal" /D "%PORTAL_ROOT%" "%APK_PYTHON%" "%PORTAL_ROOT%serve_static.py" --directory "%PORTAL_ROOT%" --port 8080 --bind 127.0.0.1

echo Starting APK frontend on http://127.0.0.1:5173 ...
start "APK Frontend" /D "%PLATFORM_ROOT%\FrontendUI\dist" "%APK_PYTHON%" "%PORTAL_ROOT%serve_static.py" --directory "%PLATFORM_ROOT%\FrontendUI\dist" --port 5173 --bind 127.0.0.1

echo Starting FastAPI on http://127.0.0.1:8000 ...
start "APK API" /D "%PLATFORM_ROOT%\apk-platform" cmd /k "set CELERY_TASK_ALWAYS_EAGER=1&& ""%APK_PYTHON%"" -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000"

echo Starting IoT frontend on http://127.0.0.1:5180 ...
start "IoT Frontend" /D "%IOT_ROOT%\frontend" cmd /k "set VITE_BACKEND=http://127.0.0.1:8100&& npm.cmd run dev -- --host 127.0.0.1 --port 5180"

echo Starting IoT API on http://127.0.0.1:8100 ...
start "IoT API" /D "%IOT_ROOT%" cmd /k "set JWT_SECRET=local-demo-only-secret-2026-08-20-at-least-32-bytes&& ""%IOT_PYTHON%"" -m uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8100"

echo.
echo All local services were started.
echo Open http://127.0.0.1:8080
endlocal
