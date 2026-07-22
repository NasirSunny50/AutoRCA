@echo off
REM ============================================================
REM  Share the AutoRCA portal + Health Checker over the internet
REM  (Cloudflare quick tunnels). Prints two public https links.
REM  NOTE: no password - anyone with the link has full access.
REM  Keep this window open; close it to take the links offline.
REM ============================================================
title AutoRCA - Share Online
powershell.exe -ExecutionPolicy Bypass -File "%~dp0share-online.ps1"
echo.
pause
