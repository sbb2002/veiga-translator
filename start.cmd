@echo off
REM Starts the two backend processes for live-translator (README step 1 + 2):
REM   1) llama-server (translation engine)
REM   2) uvicorn backend (FastAPI + WebSocket, must run from repo root)
REM Each runs in its own console window so you can see their logs / stop them
REM independently with Ctrl+C. Step 3 (loading the Chrome extension) is still
REM manual — see README.md.

setlocal
set "ROOT=%~dp0"

start "llama-server" cmd /k ""%ROOT%llama-server\llama-server.exe" -m "%ROOT%backend\models\Qwen2.5-7B-Instruct-Q4_K_M.gguf" --port 8080 -ngl 999 -c 4096"

start "backend (uvicorn)" cmd /k "cd /d "%ROOT%" && call C:\Users\User\miniconda3\Scripts\activate.bat live-translator && uvicorn backend.main:app --reload --port 8000"

endlocal
