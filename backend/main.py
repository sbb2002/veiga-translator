"""FastAPI entrypoint — Stage 2: capture + STT + translation.

Run from the repo root (so `backend.*` absolute imports resolve):
    uvicorn backend.main:app --reload --port 8000

Requires llama-server running separately (translation engine):
    llama-server/llama-server.exe -m backend/models/Qwen2.5-7B-Instruct-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
"""

from __future__ import annotations

import json
import logging

from fastapi import FastAPI, WebSocket

from backend.audio_session import AudioSession
from backend.glossary import Glossary
from backend.stt.faster_whisper_engine import FasterWhisperEngine
from backend.translation.llama_server_engine import LlamaServerEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live-translator.backend")

app = FastAPI(title="live-translator backend")

_stt_engine: FasterWhisperEngine | None = None
_translation_engine: LlamaServerEngine | None = None
_glossary: Glossary | None = None


@app.on_event("startup")
async def startup() -> None:
    global _stt_engine, _translation_engine, _glossary
    _glossary = Glossary.load()
    logger.info("Glossary loaded (%d entries) from backend/glossary.json", len(_glossary))

    logger.info("Loading STT model (this also verifies CUDA availability)...")
    # NOT wiring _glossary.whisper_hint here: `hotwords` measurably increases
    # Whisper's hallucination rate on short/ambiguous audio (see
    # faster_whisper_engine.py), and real usage confirmed it — a registered
    # glossary term (short abbreviation "TY") got hallucinated into audio
    # that never said it. Glossary still corrects translation output via
    # Glossary.translation_hint()/latin_targets() whenever STT happens to
    # transcribe the term correctly on its own; it just no longer biases STT
    # itself toward "hearing" glossary terms.
    _stt_engine = FasterWhisperEngine()
    _stt_engine.warmup()
    logger.info("STT model ready.")

    logger.info("Connecting to llama-server translation backend...")
    _translation_engine = LlamaServerEngine()
    logger.info("Translation engine ready (assumes llama-server is already running).")


@app.on_event("shutdown")
async def shutdown() -> None:
    if _translation_engine is not None:
        await _translation_engine.aclose()


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Client connected")

    async def send_event(event: dict) -> None:
        await websocket.send_text(json.dumps(event, ensure_ascii=False))

    assert _stt_engine is not None, "STT engine not initialized — startup event didn't run?"
    assert _translation_engine is not None, "Translation engine not initialized — startup event didn't run?"
    session = AudioSession(
        stt_engine=_stt_engine,
        translation_engine=_translation_engine,
        on_event=send_event,
        glossary=_glossary,
    )

    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                logger.info("Client disconnected")
                break

            if message.get("bytes") is not None:
                await session.feed_audio(message["bytes"])
            elif message.get("text") is not None:
                try:
                    control = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if control.get("type") == "stop_session":
                    logger.info("Client requested stop_session")
                    break
    finally:
        await session.close()
