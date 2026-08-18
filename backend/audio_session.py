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
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from backend import config
from backend.glossary import Glossary
from backend.sentence_completion import has_strong_sentence_boundary, looks_complete
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
    last_partial_text: str = ""

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
        # Rolling short-term context for translation continuity: the last
        # few finalized (JA, KO) sentence pairs, oldest first (see
        # config.FINAL_CONTEXT_HISTORY_SIZE / EVAL_REPORT_2026-08-18.md §5-E-1).
        self._final_history: deque[tuple[str, str]] = deque(
            maxlen=config.FINAL_CONTEXT_HISTORY_SIZE
        )
        # Finalization (beam=5 STT re-transcribe + LLM call) is too slow to
        # await inline in the frame-processing path — feed_audio() is on the
        # only path that drains incoming websocket audio, and blocking it
        # for the ~1s+ a final call can take means audio backs up and the
        # whole pipeline falls behind real time. A run-on speaker with
        # proactive mid-speech splitting (has_strong_sentence_boundary
        # below) can trigger several of these per utterance, not just one
        # at the end, so this isn't a rare case. A single background worker
        # draining a FIFO queue keeps finalize() work off the audio path
        # while still emitting "final" events (and appending _final_history)
        # in the same order the utterances were spoken.
        self._finalize_queue: asyncio.Queue[_UtteranceState] = asyncio.Queue()
        self._finalize_worker_task = asyncio.create_task(self._finalize_worker())

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

        past_silence_threshold = self._utterance.silence_ms >= config.VAD_SILENCE_MS
        past_grace_deadline = (
            self._utterance.silence_ms >= config.VAD_SILENCE_MS + config.FINALIZE_GRACE_MS
        )
        past_hard_cap = self._utterance.duration_s() >= config.MAX_UTTERANCE_SECONDS

        # Silence alone is the primary trigger, but a sentence that still
        # looks mid-thought (per sentence_completion.looks_complete) gets a
        # bounded grace period to actually finish before we force it —
        # otherwise force-finalize once the grace window elapses (or
        # immediately past the hard cap) regardless of how it looks.
        should_finalize = past_hard_cap or past_grace_deadline or (
            past_silence_threshold and looks_complete(self._utterance.last_partial_text)
        )
        if should_finalize:
            self._enqueue_finalize()
            return

        now = time.monotonic()
        enough_audio = self._utterance.duration_s() >= config.MIN_PARTIAL_AUDIO_SECONDS
        if enough_audio and now - self._utterance.last_partial_at >= config.PARTIAL_UPDATE_INTERVAL_S:
            self._utterance.last_partial_at = now
            partial_text = await self._emit_partial()
            # Run-on speakers (no pause long enough to ever cross
            # VAD_SILENCE_MS) would otherwise accumulate everything up to
            # MAX_UTTERANCE_SECONDS into a single translation call — in
            # practice this dropped whole earlier clauses from the output
            # (the model appears to compress/truncate long multi-topic
            # inputs rather than translate all of it). If the transcript so
            # far already has an unambiguous sentence ending, checkpoint it
            # as its own utterance now instead of waiting for silence that
            # may never come; the next frame naturally starts a fresh
            # _UtteranceState for the speech that's still ongoing.
            if (
                self._utterance is not None
                and self._utterance.silence_ms == 0.0
                and has_strong_sentence_boundary(partial_text)
            ):
                self._enqueue_finalize()

    async def _emit_partial(self) -> str:
        utterance = self._utterance
        if utterance is None:
            return ""
        stt_result = await asyncio.to_thread(self._stt.transcribe, utterance.audio(), fast=True)
        if not stt_result.text:
            return ""
        utterance.last_partial_text = stt_result.text
        glossary_hint = self._glossary.translation_hint(stt_result.text)
        translation = await self._translate.translate(
            stt_result.text,
            fast=True,
            glossary_hint=glossary_hint,
            allowed_literals=self._glossary.latin_targets(stt_result.text),
        )
        await self._on_event(
            {
                "type": "partial",
                "text": stt_result.text,
                "translation": translation.text,
                "segment_id": utterance.segment_id,
            }
        )
        return stt_result.text

    def _format_history(self) -> tuple[str | None, str | None]:
        """Numbered, oldest-first (JA, KO) context strings from
        self._final_history, or (None, None) when empty — matches how
        LlamaServerEngine.translate()'s `context` truthiness gates whether
        the [PREVIOUS SENTENCE] section is included at all."""
        if not self._final_history:
            return None, None
        if len(self._final_history) == 1:
            ja, ko = self._final_history[0]
            return ja, ko
        ja_lines = [f"{i}. {ja}" for i, (ja, _ko) in enumerate(self._final_history, start=1)]
        ko_lines = [f"{i}. {ko}" for i, (_ja, ko) in enumerate(self._final_history, start=1)]
        return "\n".join(ja_lines), "\n".join(ko_lines)

    def _enqueue_finalize(self) -> None:
        """Hand the current utterance off to the background finalize
        worker and immediately clear it — synchronous and cheap, so the
        frame-processing loop (and therefore audio ingestion) never blocks
        on the slow STT/translation work. Next frame's `is_speech` branch
        transparently starts a fresh _UtteranceState if speech continues."""
        utterance = self._utterance
        self._utterance = None
        if utterance is not None:
            self._finalize_queue.put_nowait(utterance)

    async def _finalize_worker(self) -> None:
        while True:
            utterance = await self._finalize_queue.get()
            try:
                await self._do_finalize(utterance)
            finally:
                self._finalize_queue.task_done()

    async def close(self) -> None:
        """Stop the background finalize worker — call when the owning
        websocket connection ends, otherwise the task leaks."""
        self._finalize_worker_task.cancel()

    async def _do_finalize(self, utterance: _UtteranceState) -> None:
        audio = utterance.audio()
        final_text = ""
        if audio.size > 0:
            stt_result = await asyncio.to_thread(self._stt.transcribe, audio, fast=False)
            final_text = stt_result.text

        if not final_text:
            # The final (beam=5) pass can come back empty even though the
            # fast partial pass found speech (e.g. no_speech_prob crossing
            # the drop threshold differently at a different beam size, more
            # likely now that proactive splitting — see
            # has_strong_sentence_boundary — creates more, shorter final
            # candidates). Silently dropping this utterance previously left
            # its frontend entry frozen on the last "partial" state forever
            # (extension/popup.js keys entries by segment_id and only
            # updates an entry when a new event for that id arrives), which
            # looked like a later sentence's translation finishing first.
            # Fall back to whatever the last partial pass transcribed so we
            # still have something to finalize with; only if there was
            # never a partial either do we emit a genuinely empty final —
            # but always emit *something* so the UI settles out of
            # "partial" state instead of hanging on it forever.
            final_text = utterance.last_partial_text

        if not final_text:
            await self._on_event(
                {"type": "final", "text": "", "translation": "", "segment_id": utterance.segment_id}
            )
            return

        glossary_hint = self._glossary.translation_hint(final_text)
        context, context_translation = self._format_history()
        translation = await self._translate.translate(
            final_text,
            fast=False,
            context=context,
            context_translation=context_translation,
            glossary_hint=glossary_hint,
            allowed_literals=self._glossary.latin_targets(final_text),
        )
        self._final_history.append((final_text, translation.text))
        await self._on_event(
            {
                "type": "final",
                "text": final_text,
                "translation": translation.text,
                "segment_id": utterance.segment_id,
            }
        )
