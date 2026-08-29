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
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import numpy as np

from backend import config
from backend.glossary import Glossary
# vanilla (backend branch reset, see docs/planning/IMPROVEMENT_BACKLOG.md M1):
# singing detection disabled — commented out, not deleted, so it can be
# restored by uncommenting this import and the call sites below.
# from backend.music_gate import MusicGate
from backend.sentence_completion import has_strong_sentence_boundary, looks_complete
from backend.stt.base import STTEngine
from backend.translation.base import TranslationEngine, TranslationResult
from backend.vad import SileroVAD

logger = logging.getLogger("live-translator.backend")

EventSink = Callable[[dict], Awaitable[None]]


def _rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio))))


@dataclass
class _UtteranceState:
    segment_id: str
    buffer: list[np.ndarray] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    last_partial_at: float = 0.0
    silence_ms: float = 0.0
    last_partial_text: str = ""
    last_partial_translation: str = ""
    enqueued_at: float = 0.0

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
        vad: SileroVAD,
        # music_gate: MusicGate,  # vanilla: singing detection disabled, see import above
        glossary: Glossary | None = None,
    ) -> None:
        self._stt = stt_engine
        self._translate = translation_engine
        self._on_event = on_event
        self._glossary = glossary or Glossary({})
        # Shared instance loaded once at startup (constructing SileroVAD here
        # meant a torch.hub model load on every websocket connection). The
        # model is a stateful RNN, so clear the previous session's state.
        # Concurrent sessions would share that state, but offscreen.js
        # guarantees a single capture at a time.
        self._vad = vad
        vad.reset()
        # self._music_gate = music_gate  # vanilla: singing detection disabled
        self._frame_buffer = np.zeros(0, dtype=np.float32)
        self._utterance: _UtteranceState | None = None
        # S5 session-adaptive VAD_SILENCE_MS/FINALIZE_GRACE_MS (see
        # config.ADAPTIVE_VAD_ENABLED). _last_speech_at is updated on every
        # speech frame regardless of utterance boundaries, so the gap
        # measured when a new utterance starts is the true silence duration
        # the speaker actually left, not our own threshold's artifact.
        self._last_speech_at: float | None = None
        self._pause_ema_ms: float | None = None
        self._pause_samples = 0
        self._rate_ema_cps: float | None = None
        self._rate_samples = 0
        # vanilla: singing-detection baseline state disabled — see
        # _record_speech_pitch_sample/_is_singing below.
        # self._speech_pitch_median_ema: float | None = None
        # self._speech_pitch_range_ema: float | None = None
        # self._speech_pitch_samples = 0
        # Rolling short-term context for translation continuity: the last
        # few final (JA, KO) sentence pairs, oldest first (see
        # config.FINAL_CONTEXT_HISTORY_SIZE / EVAL_REPORT_2026-08-18.md §5-E-1).
        self._final_history: deque[tuple[str, str]] = deque(
            maxlen=config.FINAL_CONTEXT_HISTORY_SIZE
        )
        # Wider rolling window feeding the context-summary line specifically
        # (see config.CONTEXT_SUMMARY_HISTORY_SIZE) — deliberately separate
        # from _final_history above, which stays short because it's tuned
        # for per-sentence translation continuity, not topic gist.
        self._summary_history: deque[tuple[str, str]] = deque(
            maxlen=config.CONTEXT_SUMMARY_HISTORY_SIZE
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
        # One-line "what's being talked about right now" for the extension
        # header (see config.CONTEXT_CHECK_EVERY_N_FINALS for the throttling
        # rationale). _context_summary_task doubles as the in-flight guard: a
        # new check/summary is only kicked off once the previous one has
        # actually finished. _current_summary is the last text actually
        # emitted, compared against on the next change-check; _pending_since_check
        # accumulates the (JA, KO) pairs spoken since that check so the
        # change-check call sees exactly what's new, not the whole window.
        self._finals_since_check = 0
        self._context_summary_task: asyncio.Task | None = None
        self._current_summary = ""
        self._pending_since_check: list[tuple[str, str]] = []
        # Channel name scraped from the video page (2026-08-25, see main.py's
        # start_session handling) — passed to the final translation call as
        # a [BROADCASTER] hint so the model can render the speaker's
        # self-references consistently instead of guessing a different
        # transliteration each time. None until start_session arrives (or on
        # a non-YouTube tab where the scrape never succeeds).
        self._broadcaster_hint: str | None = None

    def set_broadcaster_hint(self, channel_name: str | None) -> None:
        self._broadcaster_hint = channel_name or None

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
        # MusicGate no longer gates VAD here (2026-08-25 redesign — it now
        # judges a finished utterance's own audio for singing, synchronously
        # in _enqueue_finalize, not a live rolling window per frame; see
        # music_gate.py's module docstring).
        is_speech = self._vad.is_speech(frame)
        frame_ms = len(frame) / config.SAMPLE_RATE * 1000

        if is_speech:
            now = time.monotonic()
            if self._utterance is None:
                if config.ADAPTIVE_VAD_ENABLED and self._last_speech_at is not None:
                    self._record_pause_sample((now - self._last_speech_at) * 1000.0)
                self._utterance = _UtteranceState(segment_id=uuid.uuid4().hex)
            self._utterance.buffer.append(frame)
            self._utterance.silence_ms = 0.0
            self._last_speech_at = now
        elif self._utterance is not None:
            # Keep trailing silence in the buffer (harmless for STT) so the
            # utterance's natural tail isn't clipped mid-word.
            self._utterance.buffer.append(frame)
            self._utterance.silence_ms += frame_ms

        if self._utterance is None:
            return

        effective_silence_ms = self._effective_silence_ms()
        effective_grace_ms = self._effective_grace_ms()
        past_silence_threshold = self._utterance.silence_ms >= effective_silence_ms
        past_grace_deadline = self._utterance.silence_ms >= effective_silence_ms + effective_grace_ms
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
            if past_hard_cap:
                reason = "hard_cap"
            elif past_grace_deadline:
                reason = "grace_expired"
            else:
                reason = "silence_complete"
            logger.info(
                "finalize trigger=%s seg=%s silence_ms=%.0f dur=%.1fs eff_silence_ms=%.0f eff_grace_ms=%.0f",
                reason,
                self._utterance.segment_id,
                self._utterance.silence_ms,
                self._utterance.duration_s(),
                effective_silence_ms,
                effective_grace_ms,
            )
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
                and self._utterance.duration_s() >= config.STRONG_BOUNDARY_MIN_SECONDS
                and has_strong_sentence_boundary(partial_text)
            ):
                logger.info(
                    "finalize trigger=strong_boundary seg=%s dur=%.1fs",
                    self._utterance.segment_id,
                    self._utterance.duration_s(),
                )
                self._enqueue_finalize()

    def _record_pause_sample(self, gap_ms: float) -> None:
        """EMA-update the observed natural inter-utterance silence gap (see
        config.ADAPTIVE_SILENCE_* for the outlier/clamp rationale)."""
        if gap_ms <= 0 or gap_ms >= config.ADAPTIVE_PAUSE_OUTLIER_MS:
            return
        alpha = config.ADAPTIVE_VAD_EMA_ALPHA
        self._pause_ema_ms = (
            gap_ms if self._pause_ema_ms is None else (1 - alpha) * self._pause_ema_ms + alpha * gap_ms
        )
        self._pause_samples += 1

    def _record_rate_sample(self, char_count: int, duration_s: float) -> None:
        """EMA-update the observed speech rate (see
        config.ADAPTIVE_RATE_BASELINE_CPS)."""
        if char_count <= 0 or duration_s < 0.3:
            return
        cps = char_count / duration_s
        alpha = config.ADAPTIVE_VAD_EMA_ALPHA
        self._rate_ema_cps = (
            cps if self._rate_ema_cps is None else (1 - alpha) * self._rate_ema_cps + alpha * cps
        )
        self._rate_samples += 1

    def _effective_silence_ms(self) -> float:
        if (
            not config.ADAPTIVE_VAD_ENABLED
            or self._pause_ema_ms is None
            or self._pause_samples < config.ADAPTIVE_VAD_MIN_SAMPLES
        ):
            return config.VAD_SILENCE_MS
        target = self._pause_ema_ms * config.ADAPTIVE_SILENCE_TARGET_RATIO
        return min(max(target, config.ADAPTIVE_SILENCE_MIN_MS), config.ADAPTIVE_SILENCE_MAX_MS)

    def _effective_grace_ms(self) -> float:
        if (
            not config.ADAPTIVE_VAD_ENABLED
            or self._rate_ema_cps is None
            or self._rate_samples < config.ADAPTIVE_VAD_MIN_SAMPLES
        ):
            return config.FINALIZE_GRACE_MS
        scale = config.ADAPTIVE_RATE_BASELINE_CPS / max(self._rate_ema_cps, 0.1)
        target = config.FINALIZE_GRACE_MS * scale
        return min(max(target, config.ADAPTIVE_GRACE_MIN_MS), config.ADAPTIVE_GRACE_MAX_MS)

    # vanilla: singing-detection helpers disabled — uncomment alongside the
    # MusicGate import/state above and the _do_finalize call site below to
    # restore. See docs/planning/IMPROVEMENT_BACKLOG.md M1.
    #
    # def _record_speech_pitch_sample(self, median_hz: float, range_semitones: float) -> None:
    #     """EMA-update the session's 'how does this speaker normally talk'
    #     pitch baseline — only from utterances whose own pitch range is
    #     already narrow enough to be unambiguous plain talking (see
    #     config.ADAPTIVE_SINGING_BOOTSTRAP_RANGE_MAX_SEMITONES), so early
    #     singing can't drag the baseline it'll later be compared against."""
    #     if range_semitones > config.ADAPTIVE_SINGING_BOOTSTRAP_RANGE_MAX_SEMITONES:
    #         return
    #     alpha = config.ADAPTIVE_SINGING_EMA_ALPHA
    #     self._speech_pitch_median_ema = (
    #         median_hz
    #         if self._speech_pitch_median_ema is None
    #         else (1 - alpha) * self._speech_pitch_median_ema + alpha * median_hz
    #     )
    #     self._speech_pitch_range_ema = (
    #         range_semitones
    #         if self._speech_pitch_range_ema is None
    #         else (1 - alpha) * self._speech_pitch_range_ema + alpha * range_semitones
    #     )
    #     self._speech_pitch_samples += 1
    #
    # def _is_singing(self, median_hz: float, range_semitones: float) -> bool:
    #     """Judge whether one utterance's pitch stats look like singing
    #     rather than talking, per config.ADAPTIVE_SINGING_* — see that
    #     config block for the two-signal (range + median deviation) rationale."""
    #     if (
    #         not config.ADAPTIVE_SINGING_ENABLED
    #         or self._speech_pitch_median_ema is None
    #         or self._speech_pitch_range_ema is None
    #         or self._speech_pitch_samples < config.ADAPTIVE_SINGING_MIN_SAMPLES
    #     ):
    #         return range_semitones > config.FIXED_SINGING_RANGE_SEMITONES
    #     if range_semitones > self._speech_pitch_range_ema * config.ADAPTIVE_SINGING_RANGE_RATIO:
    #         return True
    #     median_deviation = abs(12.0 * np.log2(median_hz / self._speech_pitch_median_ema))
    #     return median_deviation > config.ADAPTIVE_SINGING_MEDIAN_DEVIATION_SEMITONES

    async def _emit_partial(self) -> str:
        utterance = self._utterance
        if utterance is None:
            return ""
        audio = utterance.audio()
        if _rms(audio) < config.AUDIO_RMS_SILENCE_FLOOR:
            # Near-silent buffer (VAD false-positive on background noise/
            # music) — skip STT entirely rather than risk a confidently
            # hallucinated partial. See config.AUDIO_RMS_SILENCE_FLOOR.
            return utterance.last_partial_text
        stt_start = time.monotonic()
        try:
            stt_result = await asyncio.to_thread(self._stt.transcribe, audio, fast=True)
        except Exception:
            logger.exception("partial STT failed — skipping this cycle")
            return utterance.last_partial_text
        stt_s = time.monotonic() - stt_start
        if not stt_result.text:
            return ""
        utterance.last_partial_text = stt_result.text
        # DEPRECATED 2026-08-19: partial (live, word-by-word) translation is
        # disabled — user call, after live capture showed the run-on
        # segmentation problem (see sentence_completion.py's
        # _TERMINAL_PUNCTUATION_RE fix) compounding with fast/beam=1 partial
        # translations of an oversized, badly-bounded buffer to produce
        # confidently wrong Korean before the sentence had even finished.
        # Per current direction: only fully "final" sentences get
        # translated; partials now surface transcription (Japanese) only.
        # This contradicts PRD §7 / CLAUDE.md's original "translate partials
        # live, literal is fine" strategy — that doc needs updating to
        # match. Left commented rather than deleted so the live-translation
        # path can be restored if this turns out to be the wrong call.
        #
        # glossary_hint = self._glossary.translation_hint(stt_result.text)
        # llm_start = time.monotonic()
        # try:
        #     translation = await self._translate.translate(
        #         stt_result.text,
        #         fast=True,
        #         glossary_hint=glossary_hint,
        #         allowed_literals=self._glossary.latin_targets(stt_result.text),
        #     )
        #     utterance.last_partial_translation = translation.text
        # except Exception:
        #     logger.exception("partial translation failed — reusing last translation")
        #     translation = TranslationResult(text=utterance.last_partial_translation)
        # llm_s = time.monotonic() - llm_start
        logger.info(
            "partial seg=%s buf=%.1fs stt=%.2fs",
            utterance.segment_id,
            utterance.duration_s(),
            stt_s,
        )
        await self._emit_safe(
            {
                "type": "partial",
                "text": stt_result.text,
                "translation": "",
                "segment_id": utterance.segment_id,
            }
        )
        return stt_result.text

    def _format_history(self) -> tuple[str | None, str | None]:
        """Numbered, oldest-first (JA, KO) context strings from
        self._final_history, or (None, None) when empty — matches how
        LlamaServerEngine.translate()'s `context` truthiness gates whether
        the [PREVIOUS SENTENCE] section is included at all."""
        return self._format_pairs(self._final_history)

    def _format_summary_history(self) -> str | None:
        """Numbered, oldest-first JA-only string from self._summary_history
        (the wider window), or None when empty — feeds summarize_context."""
        ja, _ko = self._format_pairs(self._summary_history)
        return ja

    @staticmethod
    def _format_pairs(pairs: "deque[tuple[str, str]] | list[tuple[str, str]]") -> tuple[str | None, str | None]:
        if not pairs:
            return None, None
        if len(pairs) == 1:
            ja, ko = pairs[0]
            return ja, ko
        ja_lines = [f"{i}. {ja}" for i, (ja, _ko) in enumerate(pairs, start=1)]
        ko_lines = [f"{i}. {ko}" for i, (_ja, ko) in enumerate(pairs, start=1)]
        return "\n".join(ja_lines), "\n".join(ko_lines)

    async def translate_chat(self, text: str) -> str:
        """Draft (2026-08-20): one-shot, button-triggered reverse translation
        for the viewer's own outgoing chat message — not part of the
        streaming partial/final pipeline above, but reuses the same recent
        broadcast context (self._final_history) so the phrasing fits what's
        currently happening on stream. See translation/base.py's
        translate_ko_to_ja docstring."""
        context, _context_translation = self._format_history()
        try:
            result = await self._translate.translate_ko_to_ja(text, context=context)
        except Exception:
            logger.exception("chat translation (KO->JA) failed")
            return ""
        return result.text

    async def translate_title(self, text: str) -> str:
        """One-shot JA->KO translation of the stream's own video title, for
        the overlay header — delegates to the engine's dedicated grammar-free
        title path (see LlamaServerEngine.translate_title)."""
        try:
            result = await self._translate.translate_title(text)
        except Exception:
            logger.exception("title translation (JA->KO) failed")
            return ""
        return result.text

    def _maybe_update_context_summary(self, final_text: str, translation_text: str) -> None:
        """Fire-and-forget: every config.CONTEXT_CHECK_EVERY_N_FINALS finals,
        check whether the topic actually changed (2026-08-25 redesign) before
        paying for a full summary regeneration — a speaker staying on the
        same subject for many sentences in a row shouldn't churn the summary
        line just because more finals arrived. Skips the trigger entirely
        while a previous check/summary is still in flight (rather than
        queuing another) so a slow GPU can't pile up overlapping requests
        behind the actual transcription/translation work."""
        self._pending_since_check.append((final_text, translation_text))
        self._finals_since_check += 1
        if self._finals_since_check < config.CONTEXT_CHECK_EVERY_N_FINALS:
            return
        if self._context_summary_task is not None and not self._context_summary_task.done():
            return
        self._finals_since_check = 0
        recent = self._pending_since_check
        self._pending_since_check = []
        self._context_summary_task = asyncio.create_task(self._check_and_update_context_summary(recent))

    async def _check_and_update_context_summary(self, recent: list[tuple[str, str]]) -> None:
        recent_ja, _recent_ko = self._format_pairs(recent)
        if not recent_ja:
            return
        try:
            changed = await self._translate.context_changed(self._current_summary, recent_ja)
        except Exception:
            logger.exception("context-change check failed — regenerating summary to be safe")
            changed = True
        if not changed:
            return
        ja_context = self._format_summary_history()
        if not ja_context:
            return
        try:
            summary = await self._translate.summarize_context(ja_context)
        except Exception:
            logger.exception("context summary generation failed")
            return
        if summary:
            self._current_summary = summary
            await self._emit_safe({"type": "context_summary", "text": summary})

    async def _emit_safe(self, event: dict) -> None:
        """Send an event to the client, swallowing transport errors — the
        websocket may already be gone (client closed mid-session, or events
        drained after disconnect); losing one UI update must never kill the
        pipeline or the finalize worker."""
        try:
            await self._on_event(event)
        except Exception:
            logger.warning(
                "event emit failed (client gone?) — dropped %s for seg=%s",
                event.get("type"),
                event.get("segment_id"),
            )

    def _enqueue_finalize(self) -> None:
        """Hand the current utterance off to the background finalize
        worker and immediately clear it — synchronous and cheap, so the
        frame-processing loop (and therefore audio ingestion) never blocks
        on the slow STT/translation/pitch-separation work. Next frame's
        `is_speech` branch transparently starts a fresh _UtteranceState if
        speech continues.

        Pitch stats are NOT computed here (they were, briefly, for the
        pre-Demucs pure-autocorrelation version — cheap enough at ~40ms to
        run inline; the Demucs vocal-separation pass added ahead of it is
        not, ~100-300ms, and blocking the audio-ingestion path for that on
        every single utterance would back up incoming frames). They're
        computed in _do_finalize instead, off the event loop thread via
        asyncio.to_thread — same pattern as the STT call there. This is
        safe from the old timing bug (see music_gate.py's module docstring)
        specifically because it operates on the utterance's own fixed,
        already-buffered audio, not a live/shared rolling window — running
        it later doesn't change what it sees, only when the result becomes
        available."""
        utterance = self._utterance
        self._utterance = None
        if utterance is not None:
            utterance.enqueued_at = time.monotonic()
            self._finalize_queue.put_nowait(utterance)

    async def _finalize_worker(self) -> None:
        while True:
            utterance = await self._finalize_queue.get()
            try:
                await self._do_finalize(utterance)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("finalize failed for segment %s", utterance.segment_id)
                await self._emit_safe(
                    {
                        "type": "final",
                        "text": utterance.last_partial_text,
                        "translation": utterance.last_partial_translation,
                        "segment_id": utterance.segment_id,
                    }
                )
            finally:
                self._finalize_queue.task_done()

    async def close(self) -> None:
        """Drain queued finalize work, then stop the background worker —
        call when the owning websocket connection ends, otherwise the task
        leaks. Without the drain, everything still in the queue (including
        the in-flight utterance enqueued here) would silently lose its
        "final" on stop. Bounded by CLOSE_DRAIN_TIMEOUT_S so a hung
        translation server can't stall shutdown; events emitted during the
        drain go through _emit_safe, which tolerates an already-gone
        client."""
        if self._utterance is not None:
            self._enqueue_finalize()
        try:
            await asyncio.wait_for(
                self._finalize_queue.join(), timeout=config.CLOSE_DRAIN_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.warning(
                "finalize drain timed out — dropping %d queued utterances",
                self._finalize_queue.qsize(),
            )
        self._finalize_worker_task.cancel()

    async def _do_finalize(self, utterance: _UtteranceState) -> None:
        started = time.monotonic()
        queue_wait = started - utterance.enqueued_at
        depth = self._finalize_queue.qsize()

        audio = utterance.audio()
        final_text = ""
        stt_s = 0.0
        no_speech_prob: float | None = None
        avg_logprob: float | None = None
        # vanilla: singing detection disabled — pitch_median_hz/
        # pitch_range_semitones stay None and music_suspected stays a fixed
        # False. Uncomment alongside the MusicGate import/state and
        # _is_singing/_record_speech_pitch_sample above to restore. See
        # docs/planning/IMPROVEMENT_BACKLOG.md M1.
        pitch_median_hz: float | None = None
        pitch_range_semitones: float | None = None
        music_suspected = False
        # if config.SINGING_DETECTION_ENABLED:
        #     pitch_stats = await asyncio.to_thread(self._music_gate.pitch_stats, audio)
        #     pitch_median_hz, pitch_range_semitones = pitch_stats if pitch_stats is not None else (None, None)
        #     music_suspected = (
        #         pitch_median_hz is not None
        #         and pitch_range_semitones is not None
        #         and self._is_singing(pitch_median_hz, pitch_range_semitones)
        #     )
        #     logger.info(
        #         "singing-check seg=%s pitch_hz=%s range_st=%s baseline_hz=%s baseline_range_st=%s "
        #         "samples=%d -> music_suspected=%s",
        #         utterance.segment_id,
        #         f"{pitch_median_hz:.1f}" if pitch_median_hz is not None else None,
        #         f"{pitch_range_semitones:.1f}" if pitch_range_semitones is not None else None,
        #         f"{self._speech_pitch_median_ema:.1f}" if self._speech_pitch_median_ema is not None else None,
        #         f"{self._speech_pitch_range_ema:.1f}" if self._speech_pitch_range_ema is not None else None,
        #         self._speech_pitch_samples,
        #         music_suspected,
        #     )
        dropped_low_confidence = False
        audio_rms = _rms(audio)
        # Same RMS gate as _emit_partial: don't trust a beam=5 re-transcribe
        # of near-silent audio either, so a confidently hallucinated final
        # (e.g. "最後までご視聴いただきありがとうございました" on background
        # noise with no real speech) can't slip through just because the
        # partial pass happened to skip it too. Falls through to the
        # existing last_partial_text fallback below, which will also be
        # empty in this case, settling the UI on an empty final instead of
        # showing hallucinated text.
        if audio.size > 0 and audio_rms >= config.AUDIO_RMS_SILENCE_FLOOR:
            stt_start = time.monotonic()
            try:
                stt_result = await asyncio.to_thread(self._stt.transcribe, audio, fast=False)
                stt_s = time.monotonic() - stt_start
                final_text = stt_result.text
                no_speech_prob = stt_result.no_speech_prob
                avg_logprob = stt_result.avg_logprob
                dropped_low_confidence = stt_result.dropped_low_confidence
            except Exception:
                logger.exception("final STT failed — falling back to last partial text")
                stt_s = time.monotonic() - stt_start
                final_text = ""

        if not final_text and not dropped_low_confidence:
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
            #
            # BUT skip this fallback entirely when the final pass didn't
            # just find nothing — it found something and explicitly
            # rejected it (low confidence or a known-hallucination match,
            # see faster_whisper_engine.py). Falling back to last_partial_text
            # in that case would resurrect exactly the hallucination the
            # final pass (beam=5, more reliable than the fast/beam=1 partial
            # pass that produced last_partial_text) correctly threw out —
            # observed live 2026-08-19: a "ご視聴ありがとうございました"
            # final pass got dropped by the hallucination gate, then came
            # right back via this fallback anyway.
            final_text = utterance.last_partial_text

        if not final_text:
            logger.info(
                "final seg=%s queue_wait=%.2fs depth=%d stt=%.2fs llm=%.2fs",
                utterance.segment_id,
                queue_wait,
                depth,
                stt_s,
                0.0,
            )
            await self._emit_safe(
                {
                    "type": "final",
                    "text": "",
                    "translation": "",
                    "segment_id": utterance.segment_id,
                    "audio_rms": audio_rms,
                    "no_speech_prob": no_speech_prob,
                    "avg_logprob": avg_logprob,
                    "music_suspected": music_suspected,
                }
            )
            return

        if config.ADAPTIVE_VAD_ENABLED and not music_suspected:
            # enqueued_at (captured synchronously at trigger time, before
            # this coroutine ever waited in the finalize queue) is the
            # accurate spoken duration — utterance.duration_s() would
            # instead include however long this utterance sat queued behind
            # others, badly overstating the actual speech rate.
            self._record_rate_sample(len(final_text), utterance.enqueued_at - utterance.started_at)

        # vanilla: singing-detection baseline calibration disabled (see above).
        # if (
        #     config.ADAPTIVE_SINGING_ENABLED
        #     and pitch_median_hz is not None
        #     and pitch_range_semitones is not None
        #     and not dropped_low_confidence
        #     and no_speech_prob is not None
        #     and no_speech_prob < config.WHISPER_NO_SPEECH_THRESHOLD
        # ):
        #     self._record_speech_pitch_sample(pitch_median_hz, pitch_range_semitones)

        glossary_hint = self._glossary.translation_hint(final_text)
        context, context_translation = self._format_history()
        llm_start = time.monotonic()
        try:
            translation = await self._translate.translate(
                final_text,
                fast=False,
                context=context,
                context_translation=context_translation,
                glossary_hint=glossary_hint,
                broadcaster_hint=self._broadcaster_hint,
                allowed_literals=self._glossary.latin_targets(final_text),
            )
            llm_s = time.monotonic() - llm_start
            # Don't let a music/BGM-flagged final (likely a hallucinated or
            # mistranscribed lyric — see music_gate.py) poison later
            # translations' context or the context_summary: both are built
            # straight from _final_history, so one bad entry here doesn't
            # just mis-render this segment, it drags every subsequent
            # sentence's context and the running summary off-topic too.
            if not music_suspected:
                self._final_history.append((final_text, translation.text))
                self._summary_history.append((final_text, translation.text))
                self._maybe_update_context_summary(final_text, translation.text)
        except Exception:
            logger.exception("final translation failed — falling back to last partial translation")
            llm_s = time.monotonic() - llm_start
            translation = TranslationResult(text=utterance.last_partial_translation)
        logger.info(
            "final seg=%s queue_wait=%.2fs depth=%d stt=%.2fs llm=%.2fs",
            utterance.segment_id,
            queue_wait,
            depth,
            stt_s,
            llm_s,
        )
        await self._emit_safe(
            {
                "type": "final",
                "text": final_text,
                "translation": translation.text,
                "segment_id": utterance.segment_id,
                "audio_rms": audio_rms,
                "no_speech_prob": no_speech_prob,
                "avg_logprob": avg_logprob,
                "music_suspected": music_suspected,
            }
        )
