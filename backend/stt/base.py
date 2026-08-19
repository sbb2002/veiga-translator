"""Swappable STT engine interface.

AudioSession depends only on this Protocol, never on a concrete engine
(faster-whisper, or whatever benchmarks better later — see PRD/CLAUDE.md
"STT / translation engine" policy). Swapping engines means writing a new
class satisfying STTEngine and changing the wiring in config.py, with no
changes to audio_session.py/vad.py/main.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class TranscriptionResult:
    text: str
    language: str | None = None
    # Whisper's own confidence signals for the kept segments, averaged when a
    # buffer produced more than one segment. None when there were no segments
    # to average (e.g. empty audio). Surfaced to the extension UI for
    # debugging hallucination/filtering decisions — see
    # config.WHISPER_NO_SPEECH_THRESHOLD / WHISPER_AVG_LOGPROB_THRESHOLD /
    # AUDIO_RMS_SILENCE_FLOOR.
    no_speech_prob: float | None = None
    avg_logprob: float | None = None
    # True when at least one decoded segment was explicitly rejected (low
    # confidence and/or a known-hallucination match), as opposed to Whisper
    # simply finding nothing to say. Lets callers distinguish "genuinely no
    # speech here" from "there was something, and we deliberately don't
    # trust it" — audio_session.py's finalize fallback uses this to avoid
    # reviving a rejected hallucination via a less-scrutinized earlier
    # partial-pass result (see its _do_finalize docstring note).
    dropped_low_confidence: bool = False


class STTEngine(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,  # float32 mono PCM, 16kHz, values in [-1, 1]
        *,
        fast: bool = False,  # True => optimize for latency (partial pass)
        previous_context: str | None = None,
    ) -> TranscriptionResult: ...

    def warmup(self) -> None:
        """Run a dummy inference to pay model-load/CUDA-init cost up front."""
        ...
