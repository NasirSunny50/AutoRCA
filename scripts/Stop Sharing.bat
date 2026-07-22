@echo off
REM ============================================================
REM  Take the AutoRCA share links offline (stops tunnels + apps).
REM ============================================================
title AutoRCA - Stop Sharing
powershell.exe -ExecutionPolicy Bypass -File "%~dp0stop-sharing.ps1"
echo.
pause
