"""Qwen3-ASR-{0.6B,1.7B}-hf (transformers, GPU) on the full 150-pair set.
Same output schema as transcribe_turbo.py so score_quantitative.py works
unmodified. --size selects between the two model sizes under test.

API per the model card (Qwen/Qwen3-ASR-*-hf):
processor.apply_transcription_request(audio=..., language="Japanese").

Usage: python transcribe_qwen3_asr.py --size 0.6b [--limit N]
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
DEVICE = "cuda"
DTYPE = torch.float16


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=list(MODEL_IDS), required=True)
    parser.add_argument("--limit", type=int, default=None)
    # Decoding parity (report/03-fairness-review.md): production runs this
    # engine at QWEN3_ASR_FINAL_NUM_BEAMS=5, but the original survey pass left
    # it at greedy while turbo ran beam=5. Default stays 1 to reproduce the
    # committed out/qwen3-asr-*/ baseline exactly (report/01 numbers + the
    # 20260827_vad_stt_survey B baseline depend on it). For the fair re-run
    # pass --num-beams 5 --out-suffix _fair so the original is not clobbered.
    parser.add_argument("--num-beams", type=int, default=1)
    parser.add_argument("--out-suffix", default="", help="e.g. _fair -> out/<model><suffix>/")
    args = parser.parse_args()

    model_id = MODEL_IDS[args.size]
    out_dir = Path(__file__).resolve().parents[1] / "out" / (OUT_DIR_NAMES[args.size] + args.out_suffix)

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {model_id} ({DEVICE}, {DTYPE}), num_beams={args.num_beams}...")
    processor = AutoProcessor.from_pretrained(model_id)
    model = AutoModelForMultimodalLM.from_pretrained(model_id, torch_dtype=DTYPE).to(DEVICE)
    model.eval()

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            t_start = time.monotonic()
            inputs = processor.apply_transcription_request(audio=str(seg.wav_path), language="Japanese")
            inputs = {
                k: (v.to(DEVICE, dtype=DTYPE) if torch.is_floating_point(v) else v.to(DEVICE))
                for k, v in inputs.items()
            }
            with torch.no_grad():
                output_ids = model.generate(**inputs, max_new_tokens=256, num_beams=args.num_beams)
            generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
            hyp = processor.decode(generated_ids, return_format="transcription_only")[0]
            torch.cuda.synchronize()
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
