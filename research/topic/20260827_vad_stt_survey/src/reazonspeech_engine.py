"""STTEngine adapter for ReazonSpeech-NeMo-v2 via the official
`reazonspeech.nemo.asr` wrapper (norm + pad + NeMo RNNT), for feeding it
through the real pipeline in run_pipeline.py.

Added 2026-08-28 — the 20260826 fairness re-run (report/03 §2.5) put
ReazonSpeech fair back on the table as a swap candidate, so vad_stt_survey
now runs 3 engines (turbo / qwen3-asr-1.7b / reazonspeech).

Must run in the `reazonspeech` conda env (nemo_toolkit[asr] + reazonspeech),
NOT the live-translator env — see 20260826 transcribe_reazonspeech.py's note
about nemo's dep resolution breaking torch/CUDA elsewhere.

Like Qwen3-ASR this engine exposes no per-segment confidence, so
no_speech_prob / avg_logprob stay None and only the text-embedding
HallucinationGate applies downstream. `fast` and `previous_context` are
ignored — the wrapper's TranscribeConfig only has verbose / raw_hypothesis.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


class ReazonSpeechEngine:
    def __init__(self, device: str = "cuda") -> None:
        from reazonspeech.nemo.asr import load_model

        self._model = load_model(device=device)

    def warmup(self) -> None:
        self.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), fast=True)

    def transcribe(self, audio: np.ndarray, *, fast: bool = False, previous_context=None):
        from reazonspeech.nemo.asr import audio_from_numpy, transcribe

        from backend.stt.base import TranscriptionResult

        ad = audio_from_numpy(np.ascontiguousarray(audio, dtype=np.float32), SAMPLE_RATE)
        ret = transcribe(self._model, ad)
        return TranscriptionResult(text=(ret.text or "").strip())
