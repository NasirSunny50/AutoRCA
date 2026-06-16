@echo off
REM ============================================================
REM  AutoRCA Web Portal launcher
REM  Double-click this file to open the dashboard in your browser.
REM ============================================================
cd /d "%~dp0"
title AutoRCA Portal
echo Starting the AutoRCA portal...
echo Your browser will open automatically. Keep THIS window open.
echo (Close this window or press Ctrl+C to stop the portal.)
echo.
python webapp.py
echo.
echo The portal has stopped. Press any key to close this window.
pause >nul
