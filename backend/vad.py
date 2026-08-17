"""Silero-VAD wrapper: per-frame speech/silence probability.

Chosen over webrtcvad (energy-based) because it holds up much better on
noisy/varied audio — YouTube live streams mix speech with music, sound
effects, and crowd noise, and VAD is our primary sentence-boundary signal
(PRD §7), so accuracy here matters more than raw speed.

This module deliberately only answers "how speech-like is this frame?" —
the state machine that turns a stream of probabilities into utterance
start/end events (silence-duration tracking, the hard utterance-length cap,
etc.) lives in audio_session.py, since that's the part likely to need
tuning/iteration independent of the VAD model itself.
"""

from __future__ import annotations

import numpy as np
import torch

from backend import config


class SileroVAD:
    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        threshold: float = config.VAD_SPEECH_THRESHOLD,
    ) -> None:
        model, _utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        self._model = model
        self._sample_rate = sample_rate
        self._threshold = threshold

    def reset(self) -> None:
        """Call between unrelated audio streams — the model is stateful (RNN-based)."""
        self._model.reset_states()

    def speech_probability(self, frame: np.ndarray) -> float:
        """frame: float32 mono PCM in [-1, 1], exactly config.VAD_FRAME_SAMPLES long."""
        tensor = torch.from_numpy(frame)
        with torch.no_grad():
            prob = self._model(tensor, self._sample_rate).item()
        return prob

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.speech_probability(frame) >= self._threshold
