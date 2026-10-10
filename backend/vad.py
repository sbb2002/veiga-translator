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

Two interchangeable backends, selected by config.VAD_BACKEND at construction:
  - "torch" (default): torch.hub silero-vad. torch is imported lazily here only.
  - "onnx": the same silero-vad v5 model run via onnxruntime (CPU, 1 intra-op
    thread), so torch is never imported on this path.
"""

from __future__ import annotations

import os
import urllib.request

import numpy as np

from backend import config

# One-time download source for the onnx backend (same model torch.hub fetches).
# Like the torch.hub first load, this hits the network once; afterwards the
# local file at config.VAD_ONNX_MODEL_PATH is used and no network is needed.
ONNX_MODEL_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)
# silero-vad v5 at 16kHz prepends the last 64 samples of the previous frame as context.
_ONNX_CONTEXT_SAMPLES = 64
_ONNX_STATE_SHAPE = (2, 1, 128)


def _ensure_onnx_model(path: str) -> None:
    """Download the silero-vad onnx model to `path` once if it does not exist yet."""
    if os.path.isfile(path):
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp_path = path + ".part"
    urllib.request.urlretrieve(ONNX_MODEL_URL, tmp_path)
    os.replace(tmp_path, path)


class SileroVAD:
    def __init__(
        self,
        sample_rate: int = config.SAMPLE_RATE,
        threshold: float = config.VAD_SPEECH_THRESHOLD,
    ) -> None:
        self._sample_rate = sample_rate
        self._threshold = threshold
        self._backend = config.VAD_BACKEND

        if self._backend == "torch":
            import torch  # lazy: keeps torch out of the onnx path entirely

            model, _utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            self._torch = torch
            self._model = model
        elif self._backend == "onnx":
            import onnxruntime as ort

            _ensure_onnx_model(config.VAD_ONNX_MODEL_PATH)
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            self._sess = ort.InferenceSession(
                config.VAD_ONNX_MODEL_PATH,
                sess_options=opts,
                providers=["CPUExecutionProvider"],
            )
            self._sr_input = np.array(sample_rate, dtype=np.int64)
            self._state = np.zeros(_ONNX_STATE_SHAPE, dtype=np.float32)
            self._context = np.zeros(_ONNX_CONTEXT_SAMPLES, dtype=np.float32)
        else:
            raise ValueError(f"unknown VAD_BACKEND: {self._backend!r} (expected 'torch' or 'onnx')")

    def reset(self) -> None:
        """Call between unrelated audio streams — the model is stateful (RNN-based)."""
        if self._backend == "torch":
            self._model.reset_states()
        else:
            self._state = np.zeros(_ONNX_STATE_SHAPE, dtype=np.float32)
            self._context = np.zeros(_ONNX_CONTEXT_SAMPLES, dtype=np.float32)

    def speech_probability(self, frame: np.ndarray) -> float:
        """frame: float32 mono PCM in [-1, 1], exactly config.VAD_FRAME_SAMPLES long."""
        if self._backend == "torch":
            tensor = self._torch.from_numpy(frame)
            with self._torch.no_grad():
                prob = self._model(tensor, self._sample_rate).item()
            return prob

        x = np.concatenate([self._context, frame.astype(np.float32, copy=False)])[None, :]
        out, state_n = self._sess.run(
            ["output", "stateN"],
            {"input": x, "state": self._state, "sr": self._sr_input},
        )
        self._state = state_n
        self._context = x[0, -_ONNX_CONTEXT_SAMPLES:].copy()
        return float(out[0, 0])

    def is_speech(self, frame: np.ndarray) -> bool:
        return self.speech_probability(frame) >= self._threshold
