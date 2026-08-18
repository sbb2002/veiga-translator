"""faster-whisper (CTranslate2, CUDA) implementation of STTEngine.

Chosen as the Stage 1 starting engine: CUDA support out of the box, takes a
NumPy array directly (no subprocess/CLI wrapping needed, unlike whisper.cpp),
mature Japanese support. Not the final answer — see CLAUDE.md "STT /
translation engine" — just what AudioSession is wired to today via
config.py.
"""

from __future__ import annotations

import logging
import re

import numpy as np
from faster_whisper import WhisperModel

from backend import config
from backend.stt.base import TranscriptionResult

logger = logging.getLogger("live-translator.backend")

# A second, pattern-based safety net for hallucinated stock phrases that slip
# past no_speech_prob filtering (observed: "【字幕視聴者の皆さん】" — a
# bracketed meta-commentary/shoutout the model was fairly "confident" about,
# so its no_speech_prob wasn't high enough to get caught, even though nothing
# in the audio said this). 【...】-wrapped text is a very characteristic shape
# for this specific hallucination family in Japanese auto-caption training
# data, distinct enough from real spoken dialogue to filter unconditionally.
_BRACKETED_HALLUCINATION_RE = re.compile(r"^【[^】]*】$")


class FasterWhisperEngine:
    def __init__(
        self,
        model_size: str = config.WHISPER_MODEL_SIZE,
        device: str = config.WHISPER_DEVICE,
        compute_type: str = config.WHISPER_COMPUTE_TYPE,
        language: str = config.WHISPER_LANGUAGE,
        vocabulary_hint: str = "",
    ) -> None:
        self._language = language
        self._vocabulary_hint = vocabulary_hint
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def warmup(self) -> None:
        # A ~1s silent clip is enough to pay model-load/CUDA kernel-compile
        # cost before the first real audio arrives.
        silence = np.zeros(config.SAMPLE_RATE, dtype=np.float32)
        self.transcribe(silence, fast=True)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        fast: bool = False,
        previous_context: str | None = None,
    ) -> TranscriptionResult:
        beam_size = config.WHISPER_FAST_BEAM_SIZE if fast else config.WHISPER_FINAL_BEAM_SIZE

        # Glossary vocabulary bias goes through `hotwords`, NOT `initial_prompt`.
        # initial_prompt is treated by Whisper as "this text was the transcript
        # just before this audio" — a strong prior that reliably hallucinates
        # glossary names into unrelated short/quiet audio. `hotwords` is a
        # softer bias, but still measurably increases hallucination rate on
        # short/ambiguous audio in manual testing — so it's applied only on
        # the final pass (longer, more confident buffer), never on the fast/
        # partial pass, mirroring how previous_context is already scoped.
        segments, info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=beam_size,
            condition_on_previous_text=not fast,
            initial_prompt=previous_context if not fast else None,
            hotwords=(self._vocabulary_hint or None) if not fast else None,
            word_timestamps=False,
            vad_filter=False,  # segmentation is owned by our own VAD (vad.py), not whisper's
        )

        # Whisper models are well known to hallucinate stock outro phrases
        # (e.g. "ご視聴ありがとうございました" / "字幕視聴ありがとうございました")
        # on silent/low-energy audio — an artifact of training on YouTube data
        # where auto-captioned videos commonly end with exactly that text. Our
        # own VAD gates most silence out, but not perfectly (background
        # music/noise, or trailing silence deliberately kept in the buffer —
        # see audio_session.py). `no_speech_prob` is faster-whisper's own
        # per-segment confidence that this segment isn't speech at all;
        # dropping high-confidence-silence segments filters most of these out
        # without needing a hardcoded phrase blocklist.
        words: list[tuple[str, float, float]] = []
        text_parts: list[str] = []
        for segment in segments:
            if segment.no_speech_prob >= config.WHISPER_NO_SPEECH_THRESHOLD:
                # Debug aid for diagnosing whether WHISPER_NO_SPEECH_THRESHOLD
                # is dropping real content (e.g. laughter-only audio, which
                # has no clean lexical content and can score similarly to
                # actual silence) rather than the outro-hallucination noise
                # it was tuned for — only log when the discarded segment
                # actually had non-trivial text, so routine true-silence
                # drops (near-always empty text) don't spam the log.
                if segment.text.strip():
                    logger.info(
                        "STT dropped segment (no_speech_prob=%.3f >= %.3f): %r",
                        segment.no_speech_prob,
                        config.WHISPER_NO_SPEECH_THRESHOLD,
                        segment.text,
                    )
                continue
            if _BRACKETED_HALLUCINATION_RE.match(segment.text.strip()):
                continue
            text_parts.append(segment.text)

        return TranscriptionResult(
            text="".join(text_parts).strip(),
            language=getattr(info, "language", self._language),
            words=words or None,
        )
