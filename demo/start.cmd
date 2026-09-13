@echo off
REM Portable demo launcher (see demo/INSTALL.md). Starts the two backend
REM processes for live-translator:
REM   1) llama-server (translation engine)
REM   2) uvicorn backend (FastAPI + WebSocket)
REM Both run with no visible console window, managed as a single tray icon
REM (tray_launcher.ps1) - right-click it for logs / stop. Loading the Chrome
REM extension is still manual - see demo/INSTALL.md step 5.

setlocal
set "ROOT=%~dp0"

start "" wscript.exe "%ROOT%tray_launcher.vbs"

endlocal
