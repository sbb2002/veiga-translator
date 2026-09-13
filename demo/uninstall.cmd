@echo off
REM Cleanly removes what demo/start.cmd installed on this machine (venv,
REM llama-server binaries, downloaded GGUF model, logs). Stops running
REM processes first. See demo/UNINSTALL.md for full details and what this
REM deliberately leaves alone (Chrome extension, user data).
REM
REM Add /model-cache to also remove the globally-cached downloaded STT/
REM hallucination-gate models (Hugging Face hub cache) and the silero-vad
REM cache (torch.hub) for this app specifically.

setlocal
set "EXTRA_ARGS="
if /i "%~1"=="/model-cache" set "EXTRA_ARGS=-IncludeModelCache"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %EXTRA_ARGS%
echo.
pause
endlocal
