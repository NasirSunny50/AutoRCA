@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  AutoRCA all-in-one launcher
REM  Double-click this file to run the whole project locally:
REM    - finds a working Python (even if not on PATH)
REM    - installs dependencies on first run
REM    - starts the log Monitor   (watches "Error Log File")
REM    - starts the Web Portal     (http://localhost:5000)
REM    - opens the dashboard in your browser
REM ============================================================
REM This launcher lives in the "scripts" sub-folder; run from the project root.
cd /d "%~dp0.."
title AutoRCA Launcher
echo ============================================================
echo   AutoRCA - starting up...
echo ============================================================
echo.

REM ---------- 1) Locate a REAL Python (skip Microsoft Store stub) ----------
set "PY="

REM a) Common per-user install locations
for %%V in (313 312 311 310 39) do (
    if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
)

REM b) The py launcher
if not defined PY (
    where py >nul 2>nul && (
        for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%P"
    )
)

REM c) python on PATH - but verify it actually runs (not the Store stub)
if not defined PY (
    python -c "import sys" >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [ERROR] Could not find a working Python installation.
    echo         Install Python 3.10+ from https://www.python.org/downloads/
    echo         ^(tick "Add python.exe to PATH" during setup^), then run this again.
    echo.
    pause
    exit /b 1
)

echo Using Python: !PY!
"!PY!" --version
echo.

REM ---------- 2) Ensure dependencies are installed ----------
"!PY!" -c "import flask, watchdog, yaml, requests, dotenv" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies ^(first run only^)...
    "!PY!" -m pip install --upgrade pip >nul 2>nul
    "!PY!" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed. See messages above.
        pause
        exit /b 1
    )
    echo Dependencies installed.
    echo.
)

REM ---------- 3) Start the Monitor and the Portal in their own windows ----------
echo Starting the log Monitor...
start "AutoRCA Monitor" cmd /k ""!PY!" main.py"

echo Starting the Web Portal...
start "AutoRCA Portal" cmd /k ""!PY!" webapp.py"

REM ---------- 4) Open the dashboard ----------
echo Waiting for the portal to come up...
timeout /t 4 /nobreak >nul
start "" "http://localhost:5000"

echo.
echo ============================================================
echo   AutoRCA is running.
echo   - Monitor and Portal each opened in their own window.
echo   - Dashboard: http://localhost:5000
echo   - Drop .log/.txt/.out/.trace files into "Error Log File"
echo     to get them analyzed automatically.
echo.
echo   To STOP: close the Monitor and Portal windows
echo   (or press Ctrl+C inside each).
echo ============================================================
echo.
echo You can close THIS window now.
pause >nul
endlocal
