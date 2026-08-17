"""Per-WebSocket-connection audio pipeline state.

Owns VAD-gated utterance segmentation and drives the STTEngine: while
speech is ongoing, periodically re-transcribes the in-progress buffer fast
and emits "partial"; once silence (or the hard duration cap) ends the
utterance, re-transcribes once more at higher quality and emits "final".
See PRD §7 / CLAUDE.md "Streaming / sentence-finalization strategy" for the
product rationale.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from backend import config
from backend.glossary import Glossary
from backend.stt.base import STTEngine
from backend.translation.base import TranslationEngine
from backend.vad import SileroVAD

EventSink = Callable[[dict], Awaitable[None]]


@dataclass
class _UtteranceState:
    segment_id: str
    buffer: list[np.ndarray] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    last_partial_at: float = 0.0
    silence_ms: float = 0.0

    def audio(self) -> np.ndarray:
        return np.concatenate(self.buffer) if self.buffer else np.zeros(0, dtype=np.float32)

    def duration_s(self) -> float:
        return time.monotonic() - self.started_at


class AudioSession:
    def __init__(
        self,
        stt_engine: STTEngine,
        translation_engine: TranslationEngine,
        on_event: EventSink,
        glossary: Glossary | None = None,
    ) -> None:
        self._stt = stt_engine
        self._translate = translation_engine
        self._on_event = on_event
        self._glossary = glossary or Glossary({})
        self._vad = SileroVAD()
        self._frame_buffer = np.zeros(0, dtype=np.float32)
        self._utterance: _UtteranceState | None = None
        self._last_final_text: str | None = None  # rolling context for translation continuity

    async def feed_audio(self, pcm16_bytes: bytes) -> None:
        chunk = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self._frame_buffer = np.concatenate([self._frame_buffer, chunk])

        frame_size = config.VAD_FRAME_SAMPLES
        while len(self._frame_buffer) >= frame_size:
            frame, self._frame_buffer = (
                self._frame_buffer[:frame_size],
                self._frame_buffer[frame_size:],
            )
            await self._process_frame(frame)

    async def _process_frame(self, frame: np.ndarray) -> None:
        is_speech = self._vad.is_speech(frame)
        frame_ms = len(frame) / config.SAMPLE_RATE * 1000

        if is_speech:
            if self._utterance is None:
                self._utterance = _UtteranceState(segment_id=uuid.uuid4().hex)
            self._utterance.buffer.append(frame)
            self._utterance.silence_ms = 0.0
        elif self._utterance is not None:
            # Keep trailing silence in the buffer (harmless for STT) so the
            # utterance's natural tail isn't clipped mid-word.
            self._utterance.buffer.append(frame)
            self._utterance.silence_ms += frame_ms

        if self._utterance is None:
            return

        should_finalize = (
            self._utterance.silence_ms >= config.VAD_SILENCE_MS
            or self._utterance.duration_s() >= config.MAX_UTTERANCE_SECONDS
        )
        if should_finalize:
            await self._finalize_utterance()
            return

        now = time.monotonic()
        enough_audio = self._utterance.duration_s() >= config.MIN_PARTIAL_AUDIO_SECONDS
        if enough_audio and now - self._utterance.last_partial_at >= config.PARTIAL_UPDATE_INTERVAL_S:
            self._utterance.last_partial_at = now
            await self._emit_partial()

    async def _emit_partial(self) -> None:
        utterance = self._utterance
        if utterance is None:
            return
        stt_result = await asyncio.to_thread(self._stt.transcribe, utterance.audio(), fast=True)
        if not stt_result.text:
            return
        glossary_hint = self._glossary.translation_hint(stt_result.text)
        translation = await self._translate.translate(
            stt_result.text, fast=True, glossary_hint=glossary_hint
        )
        await self._on_event(
            {
                "type": "partial",
                "text": stt_result.text,
                "translation": translation.text,
                "segment_id": utterance.segment_id,
            }
        )

    async def _finalize_utterance(self) -> None:
        utterance = self._utterance
        self._utterance = None
        if utterance is None:
            return
        audio = utterance.audio()
        if audio.size == 0:
            return
        stt_result = await asyncio.to_thread(self._stt.transcribe, audio, fast=False)
        if not stt_result.text:
            return
        glossary_hint = self._glossary.translation_hint(stt_result.text)
        translation = await self._translate.translate(
            stt_result.text,
            fast=False,
            context=self._last_final_text,
            glossary_hint=glossary_hint,
        )
        self._last_final_text = stt_result.text
        await self._on_event(
            {
                "type": "final",
                "text": stt_result.text,
                "translation": translation.text,
                "segment_id": utterance.segment_id,
            }
        )
