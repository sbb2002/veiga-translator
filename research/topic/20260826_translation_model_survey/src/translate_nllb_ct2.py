"""JA->KO with NLLB-200 (CTranslate2, CPU int8) over the eval set; same record
format as translate_llm.py so score_chrf.py --method <label> works unchanged.

Usage (from anywhere; no server needed):
    python translate_nllb_ct2.py --label p1nmt-nllb-600M --model-dir backend/models/nllb-200-distilled-600M-ct2-int8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from backend import config  # noqa: E402
from common import load_dataset  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--beam", type=int, default=None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    config.NLLB_MODEL_DIR = args.model_dir
    if args.beam:
        config.NLLB_BEAM_FINAL = args.beam
    if args.threads:
        config.NLLB_THREADS = args.threads

    from backend.translation.nllb_ct2_engine import NllbCt2Engine

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]
    out_dir = OUT_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "translations.jsonl"

    mt = NllbCt2Engine()
    await mt.verify_contract()  # warm-up, excluded from timing
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            t = time.monotonic()
            try:
                hyp = (await mt.translate(seg.ja_ref, fast=False)).text
            except Exception as e:  # noqa: BLE001
                print(f"  !! failed {seg.seg_id}: {e}")
                hyp = ""
            el = time.monotonic() - t
            f.write(json.dumps({
                "seg_id": seg.seg_id, "category": seg.category, "group": seg.group,
                "has_proper_noun": seg.has_proper_noun, "ja_ref": seg.ja_ref, "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s, "hyp_ko": hyp, "translate_elapsed_s": el,
            }, ensure_ascii=False) + "\n")
            if i % 20 == 0 or i == len(segments):
                print(f"[{i}/{len(segments)}] t={el:.2f}s -> {hyp[:40]!r}")
    print(f"done: {len(segments)} in {time.monotonic() - t0:.1f}s -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
