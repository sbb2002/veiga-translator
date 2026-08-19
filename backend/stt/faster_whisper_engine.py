"""faster-whisper (CTranslate2, CUDA) implementation of STTEngine.

Chosen as the Stage 1 starting engine: CUDA support out of the box, takes a
NumPy array directly (no subprocess/CLI wrapping needed, unlike whisper.cpp),
mature Japanese support. Not the final answer — see CLAUDE.md "STT /
translation engine" — just what AudioSession is wired to today via
config.py.
"""

from __future__ import annotations

import logging

import numpy as np
from faster_whisper import WhisperModel

from backend import config
from backend.hallucination_gate import HallucinationGate
from backend.stt.base import TranscriptionResult

logger = logging.getLogger("live-translator.backend")


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
        # Loads its own (much smaller) embedding model once — see
        # hallucination_gate.py for why this exists alongside the
        # no_speech_prob/avg_logprob check below rather than instead of it.
        self._hallucination_gate = HallucinationGate()

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
        # (e.g. "ご視聴ありがとうございました" / "最後まで視聴してくださって
        # ありがとうございます") and other training-data boilerplate on
        # silent/low-energy audio — an artifact of training on YouTube data
        # where auto-captioned videos commonly end with exactly that text. Our
        # own VAD gates most silence out, but not perfectly (background
        # music/noise, or trailing silence deliberately kept in the buffer —
        # see audio_session.py).
        #
        # Filtering this by matching the specific hallucinated wording (tried
        # first) doesn't scale: live-capture labeling on 2026-08-19
        # (data/flagged_segments.jsonl) turned up the outro phrase alone in
        # enough morphological variants that a hand-written regex for it
        # never covered the next one, and every other hallucinated phrase
        # would need its own bespoke pattern. Whisper's own no_speech_prob is
        # the general signal instead: that same labeling showed it running
        # high on essentially all of the flagged hallucinations, regardless
        # of exact wording. A single no_speech_prob threshold alone was
        # already ruled out once, though — real logs showed a MODERATE
        # no_speech_prob (0.6-0.87) alongside genuine short/energetic speech
        # ("めっちゃ嬉しい！", "僕って世界一幸せ"), so cutting there too would
        # drop real speech. Two tiers instead: a HARD threshold high enough
        # that no genuine speech has been observed to cross it, dropped
        # unconditionally; below that, fall back to the original softer
        # threshold gated by a low avg_logprob too (per OpenAI Whisper's own
        # reference decoding heuristic) so a merely-elevated no_speech_prob
        # only drops the segment when the model's own token confidence also
        # agrees it's unreliable.
        # Confidence-based filtering above catches most hallucinations but
        # provably can't separate this one recurring family (outro thank-
        # yous) from real short speech — their no_speech_prob/avg_logprob
        # ranges overlap in labeled data. The embedding-similarity gate
        # below is a second, independent check against known examples of
        # that specific family — see hallucination_gate.py.
        text_parts: list[str] = []
        kept_no_speech_probs: list[float] = []
        kept_avg_logprobs: list[float] = []
        dropped_low_confidence = False
        for segment in segments:
            stripped = segment.text.strip()
            should_drop = segment.no_speech_prob >= config.WHISPER_NO_SPEECH_HARD_THRESHOLD or (
                segment.no_speech_prob >= config.WHISPER_NO_SPEECH_THRESHOLD
                and segment.avg_logprob <= config.WHISPER_AVG_LOGPROB_THRESHOLD
            )
            matched_hallucination = None
            if not should_drop:
                matched_hallucination = self._hallucination_gate.is_known_hallucination(stripped)
                should_drop = matched_hallucination is not None
            if should_drop:
                dropped_low_confidence = True
                if stripped:
                    logger.info(
                        "STT dropped segment (no_speech_prob=%.3f, avg_logprob=%.3f%s): %r",
                        segment.no_speech_prob,
                        segment.avg_logprob,
                        f", matched known hallucination {matched_hallucination!r}"
                        if matched_hallucination
                        else "",
                        segment.text,
                    )
                continue
            text_parts.append(segment.text)
            kept_no_speech_probs.append(segment.no_speech_prob)
            kept_avg_logprobs.append(segment.avg_logprob)

        return TranscriptionResult(
            text="".join(text_parts).strip(),
            language=getattr(info, "language", self._language),
            no_speech_prob=(
                sum(kept_no_speech_probs) / len(kept_no_speech_probs)
                if kept_no_speech_probs
                else None
            ),
            avg_logprob=(
                sum(kept_avg_logprobs) / len(kept_avg_logprobs) if kept_avg_logprobs else None
            ),
            dropped_low_confidence=dropped_low_confidence,
        )
