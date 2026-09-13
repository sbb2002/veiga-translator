@echo off
REM Force-stops the demo live-translator stack (llama-server + backend +
REM tray), even when the tray icon is gone. Safe to double-click anytime.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tray_stop.ps1"
echo.
pause
