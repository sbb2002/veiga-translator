"""Same as method-1/transcribe.py but with the STT model swapped to
kotoba-tech/kotoba-whisper-v2.0-faster (HuggingFace, CTranslate2-converted
distil-whisper-style Japanese model) — user request 2026-08-22, to compare
against the currently-adopted large-v3 baseline (method-1) on the same
150-pair dataset.

faster_whisper.WhisperModel accepts a HF repo id directly as
model_size_or_path and downloads/caches it via huggingface_hub — no manual
conversion needed.

Everything else (beam_size, condition_on_previous_text=False, no VAD/
glossary/hallucination-gate, same normalization at scoring time) is kept
identical to method-1 so the comparison isolates the model swap.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from common import load_dataset
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

OUT_PATH = Path(__file__).resolve().parents[2] / "out" / "method-2" / "transcripts.jsonl"

MODEL_SIZE = "kotoba-tech/kotoba-whisper-v2.0-faster"
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float16"
LANGUAGE = "ja"
BEAM_SIZE = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {MODEL_SIZE} ({DEVICE}, {COMPUTE_TYPE})...")
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    with OUT_PATH.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            audio = decode_audio(str(seg.wav_path), sampling_rate=16000)
            t_start = time.monotonic()
            whisper_segments, info = model.transcribe(
                audio,
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                condition_on_previous_text=False,
                initial_prompt=None,
                hotwords=None,
                word_timestamps=False,
                vad_filter=False,
            )
            text_parts = []
            no_speech_probs = []
            avg_logprobs = []
            for s in whisper_segments:
                text_parts.append(s.text)
                no_speech_probs.append(s.no_speech_prob)
                avg_logprobs.append(s.avg_logprob)
            elapsed = time.monotonic() - t_start
            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": "".join(text_parts).strip(),
                "no_speech_prob": (
                    sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else None
                ),
                "avg_logprob": sum(avg_logprobs) / len(avg_logprobs) if avg_logprobs else None,
                "stt_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            if i % 10 == 0 or i == len(segments):
                print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {OUT_PATH}")


if __name__ == "__main__":
    main()
