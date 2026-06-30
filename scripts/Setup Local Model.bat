@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM  AutoRCA - one-time local model setup (run on the TARGET PC)
REM  Installs Ollama and downloads the local RCA model so AutoRCA
REM  can run fully offline with no API key.
REM
REM  After this finishes:
REM    1. set   ai.provider: "local"   in config.yaml
REM    2. run   scripts\Start AutoRCA.bat
REM ============================================================
title AutoRCA - Local Model Setup

REM ---- which model to pull (override: Setup Local Model.bat qwen2.5-coder:3b) ----
set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=qwen2.5-coder:7b"

echo ============================================================
echo   AutoRCA local model setup
echo   Model: %MODEL%
echo ============================================================
echo.

REM ---------- 1) Find or install Ollama ----------
set "OLLAMA="
where ollama >nul 2>nul && set "OLLAMA=ollama"
if not defined OLLAMA if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"

if not defined OLLAMA (
    echo Ollama not found - installing via winget...
    winget install --id Ollama.Ollama -e --source winget --accept-source-agreements --accept-package-agreements
    if errorlevel 1 (
        echo.
        echo [ERROR] Automatic install failed.
        echo         Download Ollama manually from https://ollama.com/download
        echo         install it, then run this script again.
        echo.
        pause
        exit /b 1
    )
    REM Re-locate after install (PATH may not be refreshed in this window).
    if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    where ollama >nul 2>nul && set "OLLAMA=ollama"
)

if not defined OLLAMA (
    echo [ERROR] Ollama installed but could not be located. Close this window,
    echo         open a NEW terminal, and run this script again.
    pause
    exit /b 1
)

echo Using Ollama: !OLLAMA!
"!OLLAMA!" --version
echo.

REM ---------- 2) Make sure the Ollama server is up ----------
REM The Windows installer normally starts it automatically; nudge it just in case.
start "" "!OLLAMA!" serve >nul 2>nul
echo Waiting for the Ollama server...
timeout /t 3 /nobreak >nul

REM ---------- 3) Pull the model (this is the big download) ----------
echo.
echo Downloading model %MODEL% (this can take a while on first run)...
"!OLLAMA!" pull %MODEL%
if errorlevel 1 (
    echo [ERROR] Model download failed. Check your internet connection and retry.
    pause
    exit /b 1
)

echo.
echo Installed models:
"!OLLAMA!" list
echo.
echo ============================================================
echo   Done. Local model "%MODEL%" is ready.
echo.
echo   Next:
echo     1. Open config.yaml and set:   ai.provider: "local"
echo        (and local_model: "%MODEL%" if you changed it)
echo     2. Double-click scripts\Start AutoRCA.bat
echo ============================================================
echo.
pause
endlocal
