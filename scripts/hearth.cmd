@echo off
REM Double-click this to start Hearth: Postgres if it is down, then the backend
REM and the frontend, then the browser. Ctrl-C stops the servers.
setlocal
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.start
) else (
  echo The virtualenv is missing. Run: make install
  pause
  exit /b 1
)
if errorlevel 1 pause
endlocal
