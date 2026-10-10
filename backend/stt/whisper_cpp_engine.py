"""whisper.cpp `whisper-server` (HTTP) implementation of STTEngine.

Alternative to faster_whisper_engine.py / qwen3_asr_engine.py for running
Whisper without the Python CUDA/torch stack: the STT model lives in a separate
whisper-server process and this engine only speaks HTTP to it. Selected by
config.STT_ENGINE = "whisper-cpp". This module must stay torch-free (no
torch/transformers/faster_whisper import at module level) so a torch-free
install profile can use it.

Wire contract (POST {url}/inference, multipart/form-data):
- documented fields: file (WAV), temperature, temperature_inc, prompt,
  response_format ("json" | "verbose_json").
- beam_size and language are also sent. Their support as *request* fields is
  NOT confirmed — the server documents -bs/--beam-size and -l/--language as
  startup flags. If the server ignores the request-level beam_size, the
  server's own -bs value applies. For different fast/final beams, point
  config.WHISPER_CPP_FINAL_URL at a second server started with -bs 5 (and
  leave WHISPER_CPP_FAST_URL on a -bs 1 server).
- response parsing is defensive: with verbose_json, `segments` (each with
  `text`, and possibly `no_speech_prob` / `avg_logprob`) are used when present;
  with plain json only the top-level `text` is available.

Confidence filtering (no_speech_prob / avg_logprob thresholds, the same
two-tier rule as faster_whisper_engine.py) is applied ONLY to segments that
actually carry those fields. Where the server does not expose them, that
layer is inert — same situation as qwen3_asr_engine.py — and only the
text-based embedding-similarity HallucinationGate applies. The RMS silence
gate lives in audio_session.py and is not duplicated here.
"""

from __future__ import annotations

import io
import logging
import wave

import httpx
import numpy as np

from backend import config
from backend.hallucination_gate import HallucinationGate
from backend.stt.base import TranscriptionResult

logger = logging.getLogger("live-translator.backend")


def _encode_wav(audio: np.ndarray, sample_rate: int = config.SAMPLE_RATE) -> bytes:
    """float32 mono [-1, 1] -> in-memory 16-bit PCM WAV."""
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm16 = (clipped * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


class WhisperCppEngine:
    def __init__(
        self,
        fast_url: str = config.WHISPER_CPP_FAST_URL,
        final_url: str = config.WHISPER_CPP_FINAL_URL,
        timeout_s: float = config.WHISPER_CPP_TIMEOUT_S,
        language: str = config.WHISPER_LANGUAGE,
    ) -> None:
        self._fast_url = fast_url.rstrip("/")
        # Empty WHISPER_CPP_FINAL_URL => one server handles both passes.
        self._final_url = (final_url or fast_url).rstrip("/")
        self._language = language
        self._client = httpx.Client(timeout=timeout_s)
        # Same text-level known-hallucination gate the other engines use. It is
        # the only hallucination defense that works without segment confidence.
        self._hallucination_gate = HallucinationGate()

    def warmup(self) -> None:
        # 1s of silence through the fast path: pays the server-side model load
        # cost up front and fails fast with a clear message if unreachable.
        silence = np.zeros(config.SAMPLE_RATE, dtype=np.float32)
        self.transcribe(silence, fast=True)

    def _post(self, base_url: str, wav: bytes, fields: dict[str, str]) -> dict:
        url = f"{base_url}/inference"
        try:
            response = self._client.post(
                url,
                files={"file": ("audio.wav", wav, "audio/wav")},
                data=fields,
            )
        except httpx.TransportError as exc:
            raise RuntimeError(
                f"whisper-server unreachable at {url} ({type(exc).__name__}: {exc}). "
                "Start whisper-server or fix config.WHISPER_CPP_FAST_URL / "
                "WHISPER_CPP_FINAL_URL."
            ) from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"whisper-server at {url} returned HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"whisper-server at {url} returned non-JSON body") from exc
        if not isinstance(body, dict):
            raise RuntimeError(f"whisper-server at {url} returned unexpected JSON shape")
        return body

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        fast: bool = False,
        previous_context: str | None = None,
    ) -> TranscriptionResult:
        base_url = self._fast_url if fast else self._final_url
        beam_size = config.WHISPER_FAST_BEAM_SIZE if fast else config.WHISPER_FINAL_BEAM_SIZE

        fields: dict[str, str] = {
            "temperature": "0.0",
            # Disable whisper.cpp's temperature-fallback resampling so output
            # stays deterministic per buffer, matching the greedy/beam settings.
            "temperature_inc": "0.0",
            "response_format": "verbose_json",
            "language": self._language,
            "beam_size": str(beam_size),
        }
        # previous_context is prompt-only and final-pass-only, mirroring
        # faster_whisper_engine.py (initial_prompt scoped the same way).
        if not fast and previous_context:
            fields["prompt"] = previous_context

        body = self._post(base_url, _encode_wav(audio), fields)
        raw_segments = body.get("segments")
        language = body.get("language") or self._language

        if not isinstance(raw_segments, list):
            # Plain json response, or verbose_json without segments: only the
            # whole-text gate is available.
            text = str(body.get("text", "")).strip()
            matched = self._hallucination_gate.is_known_hallucination(text) if text else None
            if matched is not None:
                logger.info("STT dropped text (matched known hallucination %r): %r", matched, text)
                return TranscriptionResult(
                    text="", language=language, dropped_low_confidence=True
                )
            return TranscriptionResult(text=text, language=language)

        text_parts: list[str] = []
        kept_no_speech_probs: list[float] = []
        kept_avg_logprobs: list[float] = []
        dropped_low_confidence = False
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            stripped = str(segment.get("text", "")).strip()
            no_speech = segment.get("no_speech_prob")
            avg_logprob = segment.get("avg_logprob")
            no_speech = float(no_speech) if isinstance(no_speech, (int, float)) else None
            avg_logprob = float(avg_logprob) if isinstance(avg_logprob, (int, float)) else None

            # Two-tier confidence rule, same as faster_whisper_engine.py. Each
            # tier needs its own field; missing fields make that tier inert.
            should_drop = no_speech is not None and (
                no_speech >= config.WHISPER_NO_SPEECH_HARD_THRESHOLD
                or (
                    no_speech >= config.WHISPER_NO_SPEECH_THRESHOLD
                    and avg_logprob is not None
                    and avg_logprob <= config.WHISPER_AVG_LOGPROB_THRESHOLD
                )
            )
            matched_hallucination = None
            if not should_drop and stripped:
                matched_hallucination = self._hallucination_gate.is_known_hallucination(stripped)
                should_drop = matched_hallucination is not None
            if should_drop:
                dropped_low_confidence = True
                if stripped:
                    logger.info(
                        "STT dropped segment (no_speech_prob=%s, avg_logprob=%s%s): %r",
                        no_speech,
                        avg_logprob,
                        f", matched known hallucination {matched_hallucination!r}"
                        if matched_hallucination
                        else "",
                        stripped,
                    )
                continue
            text_parts.append(str(segment.get("text", "")))
            if no_speech is not None:
                kept_no_speech_probs.append(no_speech)
            if avg_logprob is not None:
                kept_avg_logprobs.append(avg_logprob)

        return TranscriptionResult(
            text="".join(text_parts).strip(),
            language=language,
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
