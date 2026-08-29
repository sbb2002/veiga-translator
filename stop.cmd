@echo off
REM Force-stops the live-translator stack (llama-server + backend + tray),
REM even when the tray icon is gone. Safe to double-click anytime.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tray_stop.ps1"
echo.
pause
