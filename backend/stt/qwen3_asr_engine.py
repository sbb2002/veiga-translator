"""Qwen3-ASR-1.7B-hf (transformers, CUDA) implementation of STTEngine.

Swapped in 2026-08-26 after research/topic/20260826_stt_model_survey_gpu_full/
found this model statistically tied with large-v3-turbo on quality (CER/BLEU/
ROUGE-L CIs overlap; chrF++ point estimate is actually higher) across the full
150-pair benchmark. Two things that benchmark did NOT establish, carried over
as known risk here:

- **RTF is ~3.2x turbo's** (0.106 vs 0.033, ~0.33s more per ~4.5s utterance).
  The benchmark only measured one-shot full-clip transcription, never this
  model's behavior under this app's repeated-call-on-a-growing-buffer partial
  pattern (`fast=True`, called every ~PARTIAL_UPDATE_INTERVAL_S) — that access
  pattern is unverified and could be considerably worse than the one-shot RTF
  suggests, since cost may not scale linearly with buffer growth the way
  streaming-native decoders do.
- **No per-segment confidence signal.** Unlike faster-whisper's
  no_speech_prob/avg_logprob (which drive WHISPER_NO_SPEECH_THRESHOLD/
  WHISPER_AVG_LOGPROB_THRESHOLD in faster_whisper_engine.py), a plain
  `generate()` call here exposes no equivalent — this engine always returns
  no_speech_prob=None, avg_logprob=None, so that entire confidence-threshold
  layer of hallucination filtering is inert for this engine. The embedding-
  similarity HallucinationGate (hallucination_gate.py) is engine-agnostic
  (operates on output text only) and is preserved here as the sole remaining
  hallucination defense — a narrower safety net than before.
"""

from __future__ import annotations

import logging

import numpy as np
import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor

from backend import config
from backend.hallucination_gate import HallucinationGate
from backend.stt.base import TranscriptionResult

logger = logging.getLogger("live-translator.backend")


class Qwen3ASREngine:
    def __init__(
        self,
        model_id: str = config.QWEN3_ASR_MODEL_ID,
        device: str = config.QWEN3_ASR_DEVICE,
        language: str = config.QWEN3_ASR_LANGUAGE,
    ) -> None:
        self._language = language
        self._device = device
        self._dtype = torch.float16
        self._processor = AutoProcessor.from_pretrained(model_id)
        self._model = AutoModelForMultimodalLM.from_pretrained(model_id, torch_dtype=self._dtype).to(device)
        self._model.eval()
        # Same known-hallucination text gate faster_whisper_engine.py uses —
        # see this module's docstring for why it's the only hallucination
        # defense left for this engine (no confidence-score equivalent).
        self._hallucination_gate = HallucinationGate()

    def warmup(self) -> None:
        silence = np.zeros(config.SAMPLE_RATE, dtype=np.float32)
        self.transcribe(silence, fast=True)

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        fast: bool = False,
        previous_context: str | None = None,
    ) -> TranscriptionResult:
        max_new_tokens = config.QWEN3_ASR_FAST_MAX_NEW_TOKENS if fast else config.QWEN3_ASR_FINAL_MAX_NEW_TOKENS
        num_beams = 1 if fast else config.QWEN3_ASR_FINAL_NUM_BEAMS

        # `prompt` is Qwen3-ASR's own context/hotwords channel (system prompt
        # ahead of the audio turn) — the closest equivalent to faster-whisper's
        # initial_prompt, scoped the same way (final pass only, never fast/
        # partial — mirrors faster_whisper_engine.py's reasoning that priming
        # short/ambiguous audio measurably increases hallucination rate).
        inputs = self._processor.apply_transcription_request(
            audio=audio,
            language=self._language,
            prompt=previous_context if not fast else None,
        )
        inputs = {
            k: (v.to(self._device, dtype=self._dtype) if torch.is_floating_point(v) else v.to(self._device))
            for k, v in inputs.items()
        }

        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                num_beams=num_beams,
                do_sample=False,
            )
        generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
        hyp = self._processor.decode(generated_ids, return_format="transcription_only")[0].strip()

        dropped_low_confidence = False
        if hyp:
            matched_hallucination = self._hallucination_gate.is_known_hallucination(hyp)
            if matched_hallucination is not None:
                logger.info(
                    "STT dropped segment (matched known hallucination %r): %r",
                    matched_hallucination,
                    hyp,
                )
                dropped_low_confidence = True
                hyp = ""

        return TranscriptionResult(
            text=hyp,
            language=self._language,
            no_speech_prob=None,
            avg_logprob=None,
            dropped_low_confidence=dropped_low_confidence,
        )
