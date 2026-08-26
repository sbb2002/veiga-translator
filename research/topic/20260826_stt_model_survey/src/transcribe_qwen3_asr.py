"""Qwen3-ASR-{0.6B,1.7B}-hf (transformers, CPU) on the pilot subset. Same
output schema as transcribe_turbo.py so score_quantitative.py works
unmodified. --size selects between the two model sizes under test.

API per the model card (Qwen/Qwen3-ASR-*-hf):
processor.apply_transcription_request(audio=..., language="Japanese").

Usage: python transcribe_qwen3_asr.py --size 0.6b [--pilot-n 5] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import torch
from common import load_dataset
from transformers import AutoModelForMultimodalLM, AutoProcessor

MODEL_IDS = {
    "0.6b": "Qwen/Qwen3-ASR-0.6B-hf",
    "1.7b": "Qwen/Qwen3-ASR-1.7B-hf",
}
OUT_DIR_NAMES = {
    "0.6b": "qwen3-asr-0.6b",
    "1.7b": "qwen3-asr-1.7b",
}
DEVICE = "cpu"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=list(MODEL_IDS), required=True)
    parser.add_argument("--pilot-n", type=int, default=5, help="segments per category; 0 = full 150")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    model_id = MODEL_IDS[args.size]
    out_dir = Path(__file__).resolve().parents[1] / "out" / OUT_DIR_NAMES[args.size]

    segments = load_dataset(pilot_n_per_category=args.pilot_n or None)
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {model_id} ({DEVICE})...")
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForMultimodalLM.from_pretrained(model_id, torch_dtype=torch.float32)
    model.eval()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            t_start = time.monotonic()
            inputs = processor.apply_transcription_request(audio=str(seg.wav_path), language="Japanese")
            with torch.no_grad():
                output_ids = model.generate(**inputs, max_new_tokens=256)
            generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
            hyp = processor.decode(generated_ids, return_format="transcription_only")[0]
            elapsed = time.monotonic() - t_start

            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": hyp.strip(),
                "stt_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s -> {hyp.strip()[:40]!r}")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
