@echo off
REM Starts the two backend processes for live-translator (README step 1 + 2):
REM   1) llama-server (translation engine)
REM   2) uvicorn backend (FastAPI + WebSocket, must run from repo root)
REM Both run with no visible console window, managed as a single tray icon
REM (tray_launcher.ps1) - right-click it for logs / stop. Step 3 (loading the
REM Chrome extension) is still manual - see README.md.

setlocal
set "ROOT=%~dp0"

start "" wscript.exe "%ROOT%tray_launcher.vbs"

endlocal
