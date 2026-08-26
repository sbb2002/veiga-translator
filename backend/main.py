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
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket

from backend.audio_session import AudioSession
from backend.glossary import Glossary
# vanilla (backend branch reset, see docs/planning/IMPROVEMENT_BACKLOG.md M1):
# singing detection disabled — commented out, not deleted.
# from backend.music_gate import MusicGate
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

# Per-session transcript/translation logs (2026-08-24): one jsonl file per
# capture session, written alongside FLAGGED_SEGMENTS_LOG above but covering
# the whole session rather than just user-flagged segments — nothing durable
# survived a session before this (see chrome.storage.session's docstring in
# extension/background.js: cleared on browser close, never exported).
SESSION_LOGS_DIR = Path("data/sessions")


class SessionLogger:
    """Writes one jsonl file per capture session: a "session_start" header
    line carrying the captured tab's metadata (title/url/tab_id, plus the
    extension-side start timestamp), followed by one line per event sent to
    the client (partial/final/context_summary/chat_translation), each
    stamped with wall-clock time. Opened lazily on the "start_session"
    control message (that's the first point metadata is available — see
    offscreen.js's connectWebSocket) rather than at websocket-accept time."""

    def __init__(self) -> None:
        self._file = None  # type: ignore[assignment]

    def start(self, control: dict) -> None:
        SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        now = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        tab_id = control.get("tab_id")
        suffix = uuid.uuid4().hex[:6]
        tab_part = f"tab{tab_id}" if tab_id is not None else "tab"
        path = SESSION_LOGS_DIR / f"{stamp}_{tab_part}_{suffix}.jsonl"
        self._file = path.open("a", encoding="utf-8")
        self._write(
            {
                "type": "session_start",
                "timestamp": now,
                "title": control.get("title"),
                "url": control.get("url"),
                "tab_id": tab_id,
                # Client-side Date.now() (ms) when capture started — distinct
                # from `timestamp` above, which is when this log file/backend
                # session actually opened (can lag slightly behind).
                "client_started_at_ms": control.get("started_at"),
                "sample_rate": control.get("sample_rate"),
                # Scraped by extension/content_script.js from the YouTube
                # page's own ytInitialPlayerResponse (2026-08-25) — richer
                # than title/url alone, and channel_name doubles as the
                # AudioSession broadcaster-hint (see main.py's start_session
                # handling below).
                "channel_name": control.get("channel_name"),
                "video_title": control.get("video_title"),
                "video_id": control.get("video_id"),
                "is_live": control.get("is_live"),
                "stream_started_at": control.get("stream_started_at"),
            }
        )
        logger.info("session log started: %s", path)

    def log_event(self, event: dict) -> None:
        if self._file is None:
            return
        self._write({"timestamp": time.time(), **event})

    def _write(self, entry: dict) -> None:
        if self._file is None:
            return
        self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is None:
            return
        self._write({"type": "session_end", "timestamp": time.time()})
        self._file.close()
        self._file = None


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
# _music_gate: MusicGate | None = None  # vanilla: singing detection disabled


@app.on_event("startup")
async def startup() -> None:
    global _stt_engine, _translation_engine, _glossary, _vad
    _glossary = Glossary.load()
    logger.info("Glossary loaded (%d entries) from backend/glossary.json", len(_glossary))

    # Loaded once and shared across sessions — AudioSession resets its RNN
    # state per connection (see audio_session.py).
    logger.info("Loading silero-VAD model...")
    _vad = SileroVAD()
    logger.info("VAD model ready.")

    # vanilla: singing-detection model loading disabled (see
    # docs/planning/IMPROVEMENT_BACKLOG.md M1). Uncomment alongside the
    # MusicGate import/global above and the AudioSession kwarg below to
    # restore.
    # logger.info("Loading singing-detection (Demucs vocal separation) model...")
    # _music_gate = MusicGate()
    # _music_gate.warmup()
    # logger.info("Singing-detection model ready.")

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


@app.on_event("shutdown")
async def shutdown() -> None:
    if _translation_engine is not None:
        await _translation_engine.aclose()


@app.websocket("/ws/audio")
async def ws_audio(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("Client connected")
    session_log = SessionLogger()

    async def send_event(event: dict) -> None:
        session_log.log_event(event)
        await websocket.send_text(json.dumps(event, ensure_ascii=False))

    assert _stt_engine is not None, "STT engine not initialized — startup event didn't run?"
    assert _translation_engine is not None, "Translation engine not initialized — startup event didn't run?"
    assert _vad is not None, "VAD not initialized — startup event didn't run?"
    # assert _music_gate is not None, "MusicGate not initialized — startup event didn't run?"  # vanilla
    session = AudioSession(
        stt_engine=_stt_engine,
        translation_engine=_translation_engine,
        on_event=send_event,
        vad=_vad,
        # music_gate=_music_gate,  # vanilla: singing detection disabled
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
                if control.get("type") == "start_session":
                    session_log.start(control)
                    session.set_broadcaster_hint(control.get("channel_name"))
                if control.get("type") == "metadata_update":
                    # Mid-session video switch (2026-08-25) — extension's
                    # content_script.js detected a YouTube SPA navigation to
                    # a new video/live within the same tab (see
                    # offscreen.js). Log it as its own event (so a later
                    # review sees exactly when/what changed, distinct from
                    # the original session_start header) and refresh the
                    # [BROADCASTER] hint for the new streamer.
                    logger.info(
                        "session metadata updated: channel=%r video_title=%r",
                        control.get("channel_name"),
                        control.get("video_title"),
                    )
                    session_log.log_event(
                        {
                            "type": "metadata_update",
                            "title": control.get("title"),
                            "url": control.get("url"),
                            "channel_name": control.get("channel_name"),
                            "video_title": control.get("video_title"),
                            "video_id": control.get("video_id"),
                            "is_live": control.get("is_live"),
                            "stream_started_at": control.get("stream_started_at"),
                        }
                    )
                    session.set_broadcaster_hint(control.get("channel_name"))
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
                if control.get("type") == "translate_chat":
                    # Draft (2026-08-20): one-shot KO->JA translation for the
                    # viewer's own outgoing chat message — see
                    # audio_session.py::translate_chat. Not on the streaming
                    # partial/final path, so handled inline here rather than
                    # via feed_audio().
                    request_id = control.get("request_id")
                    text = control.get("text", "")
                    translation = await session.translate_chat(text)
                    await send_event(
                        {
                            "type": "chat_translation",
                            "request_id": request_id,
                            "text": text,
                            "translation": translation,
                        }
                    )
    finally:
        await session.close()
        session_log.close()
