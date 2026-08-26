"""Baseline: large-v3-turbo (faster-whisper, GPU) on the full 150-pair set.
Same device/compute_type as production (`backend/stt/` — CLAUDE.md: CUDA,
int8_float16) so RTF here is representative of the actual deployed pipeline,
not just a benchmark artifact.

Usage: python transcribe_turbo.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from common import load_dataset
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "turbo"

MODEL_SIZE = "large-v3-turbo"
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            audio = decode_audio(str(seg.wav_path), sampling_rate=16000)
            t_start = time.monotonic()
            whisper_segments, info = model.transcribe(
                audio,
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                condition_on_previous_text=False,
                vad_filter=False,
            )
            text_parts = [s.text for s in whisper_segments]
            elapsed = time.monotonic() - t_start
            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": "".join(text_parts).strip(),
                "stt_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
