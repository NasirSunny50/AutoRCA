@echo off
REM ============================================================
REM  AutoRCA Log Monitor launcher
REM  Double-click to start watching the "Error Log File" folder.
REM  Drop .log/.txt/.out/.trace files in there to get them analyzed.
REM ============================================================
cd /d "%~dp0"
title AutoRCA Monitor
echo Starting the AutoRCA log monitor...
echo Drop log files into the "Error Log File" folder to analyze them.
echo Keep THIS window open. Press Ctrl+C to stop.
echo.
python main.py
echo.
echo The monitor has stopped. Press any key to close this window.
pause >nul
