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


class STTEngine(Protocol):
    def transcribe(
        self,
        audio: np.ndarray,  # float32 mono PCM, 16kHz, values in [-1, 1]
        *,
        fast: bool = False,  # True => optimize for latency (provisional pass)
        previous_context: str | None = None,
    ) -> TranscriptionResult: ...

    def warmup(self) -> None:
        """Run a dummy inference to pay model-load/CUDA-init cost up front."""
        ...
