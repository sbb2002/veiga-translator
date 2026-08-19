"""Interface for per-utterance speaker labeling — same swappable-engine
principle as backend/stt/base.py and backend/translation/base.py: a future
implementation drops in as a new class with no changes to audio_session.py.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class SpeakerIdentifier(Protocol):
    def identify(self, audio: np.ndarray, sample_rate: int) -> str | None:
        """Return a label (e.g. "화자 1") for who most likely spoke this whole
        utterance's audio, or None when identification wasn't attempted
        (audio too short, model unavailable, etc.) — callers must treat None
        as "unlabeled", not as a speaker in its own right."""
        ...

    def reset(self) -> None:
        """Clear registered speakers. Call once per capture session (not
        shared across unrelated sessions) — see the implementation's
        docstring for why this state is per-session rather than global."""
        ...
