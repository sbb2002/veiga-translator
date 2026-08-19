"""FastAPI entrypoint — Stage 2: capture + STT + translation.

Run from the repo root (so `backend.*` absolute imports resolve):
    uvicorn backend.main:app --reload --port 8000

Requires a llama.cpp server (CUDA build) serving gemma-3-12b-it separately, e.g.:
    llama-server.exe -m <path-to>/google_gemma-3-12b-it-Q4_K_M.gguf --port 8080 -ngl 999 -c 4096
Note: the repo-bundled llama-server/ is a CPU-only build and backend/models/
only holds the old Qwen GGUF — the production binary/model live outside this
repo (GPU machine); record their actual paths here once confirmed. Startup
probes the server (verify_contract) and warns if it is unreachable or does
not honor GBNF grammar.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket

from backend import config
from backend.audio_session import AudioSession
from backend.glossary import Glossary
from backend.speaker_id.base import SpeakerIdentifier
from backend.stt.faster_whisper_engine import FasterWhisperEngine
from backend.translation.llama_server_engine import LlamaServerEngine
from backend.vad import SileroVAD

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("live-translator.backend")

app = FastAPI(title="live-translator backend")

# Manual mislabel tagging from the popup (see extension/popup.js — click a
# finalized sentence to flag it wrong, click again to undo). Appended as-is,
# one JSON object per line, so a live-capture review pass can grep/jq this
# alongside the eval-set jsonl files under data/.
FLAGGED_SEGMENTS_LOG = Path("data/flagged_segments.jsonl")


def _append_flag(control: dict) -> None:
    entry = {
        "timestamp": time.time(),
        "segment_id": control.get("segment_id"),
        "flagged": control.get("flagged"),
        "text": control.get("text"),
        "translation": control.get("translation"),
        "audio_rms": control.get("audio_rms"),
        "no_speech_prob": control.get("no_speech_prob"),
        "avg_logprob": control.get("avg_logprob"),
    }
    with FLAGGED_SEGMENTS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

_stt_engine: FasterWhisperEngine | None = None
_translation_engine: LlamaServerEngine | None = None
_glossary: Glossary | None = None
_vad: SileroVAD | None = None
_speaker_identifier: SpeakerIdentifier | None = None


@app.on_event("startup")
async def startup() -> None:
    global _stt_engine, _translation_engine, _glossary, _vad, _speaker_identifier
    _glossary = Glossary.load()
    logger.info("Glossary loaded (%d entries) from backend/glossary.json", len(_glossary))

    # Loaded once and shared across sessions — AudioSession resets its RNN
    # state per connection (see audio_session.py).
    logger.info("Loading silero-VAD model...")
    _vad = SileroVAD()
    logger.info("VAD model ready.")

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
    await _translation_engine.verify_contract()
    logger.info("Translation engine ready (assumes llama-server is already running).")

    # 2차 목표 (다중 화자) — optional: a backend without speechbrain installed,
    # or with SPEAKER_ID_ENABLED off, still runs 1차 목표 (single-speaker)
    # capture exactly as before, just without speaker tags in the UI.
    if config.SPEAKER_ID_ENABLED:
        try:
            logger.info("Loading speaker-identification model (%s)...", config.SPEAKER_ID_MODEL)
            from backend.speaker_id.speechbrain_engine import SpeechbrainSpeakerIdentifier

            _speaker_identifier = SpeechbrainSpeakerIdentifier()
            logger.info("Speaker-identification model ready.")
        except Exception:
            logger.exception(
                "Speaker-identification model failed to load — continuing without speaker "
                "labels (pip install speechbrain? or set config.SPEAKER_ID_ENABLED = False)"
            )
            _speaker_identifier = None


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
    assert _vad is not None, "VAD not initialized — startup event didn't run?"
    session = AudioSession(
        stt_engine=_stt_engine,
        translation_engine=_translation_engine,
        on_event=send_event,
        vad=_vad,
        glossary=_glossary,
        speaker_identifier=_speaker_identifier,
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
                if control.get("type") == "flag_segment":
                    logger.info(
                        "segment flagged=%s seg=%s: %r",
                        control.get("flagged"),
                        control.get("segment_id"),
                        control.get("text"),
                    )
                    _append_flag(control)
    finally:
        await session.close()
