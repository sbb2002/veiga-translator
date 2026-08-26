"""Translate ja_ref -> hyp_ko_from_ref for Seed-X-Instruct-7B, served by
llama-server (start it yourself first — see README.md). Seed-X does NOT use a
chat template (model card: "we don't have any chat template, thus you don't
have to perform tokenizer.apply_chat_template") — it's a raw-completion model
with a fixed prompt format:

    Translate the following Japanese sentence into Korean:
    {text} <ko>

So this hits llama-server's raw /completion endpoint directly, not
/v1/chat/completions (which LlamaServerEngine/translate_llm.py use) — a
generic chat wrapper would apply whatever template the GGUF conversion
embedded (if any), which doesn't match what this model was tuned on.

Usage (llama-server must already be running with the Seed-X GGUF):
    python translate_seedx.py --label seed-x-instruct-7b
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

import httpx
from common import load_dataset

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
LLAMA_SERVER_URL = "http://127.0.0.1:8080"


def build_prompt(ja_text: str) -> str:
    return f"Translate the following Japanese sentence into Korean:\n{ja_text} <ko>"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="out/<label>/ subdir name")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    out_dir = OUT_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "translations.jsonl"

    t0 = time.monotonic()
    async with httpx.AsyncClient(base_url=LLAMA_SERVER_URL, timeout=120.0) as client:
        with out_path.open("w", encoding="utf-8") as out_f:
            for i, seg in enumerate(segments, 1):
                prompt = build_prompt(seg.ja_ref)
                t_start = time.monotonic()
                try:
                    resp = await client.post(
                        "/completion",
                        json={
                            "prompt": prompt,
                            "n_predict": 200,
                            "temperature": 0.0,
                            "stop": ["\n"],
                        },
                    )
                    resp.raise_for_status()
                    hyp = resp.json().get("content", "").strip()
                except Exception as e:  # noqa: BLE001
                    print(f"  !! translate failed for {seg.seg_id}: {e}")
                    hyp = ""
                elapsed = time.monotonic() - t_start

                record = {
                    "seg_id": seg.seg_id,
                    "category": seg.category,
                    "group": seg.group,
                    "has_proper_noun": seg.has_proper_noun,
                    "ja_ref": seg.ja_ref,
                    "ko_ref": seg.ko_ref,
                    "duration_s": seg.duration_s,
                    "hyp_ko": hyp,
                    "translate_elapsed_s": elapsed,
                }
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()
                print(f"[{i}/{len(segments)}] {seg.seg_id} t={elapsed:.2f}s -> {hyp[:40]!r}")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
