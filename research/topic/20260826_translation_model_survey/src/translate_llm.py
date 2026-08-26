"""Translate ja_ref -> hyp_ko_from_ref for one candidate LLM already served
by llama-server (start it yourself first, pointed at the candidate GGUF —
see README.md for the per-model commands used in this survey). Pure-MT-
quality condition (STT error excluded), matching every prior translation
benchmark in this repo (docs/eval/EVAL.md, research/topic/
20260818_translation_model_benchmark/) — only the final/full-quality pass is
measured, not the fast/partial path (partial translation is app-wide
deprecated, see CLAUDE.md's streaming-strategy section).

Records wall-clock translate() time per segment (RTF = total_translate_s /
total_audio_duration_s, same convention as the STT surveys — how much of the
source audio's own duration the translation step would consume if run
inline).

Usage (llama-server must already be running with the candidate model):
    python translate_llm.py --label gemma-3-12b-it
    python translate_llm.py --label qwen3-14b --no-grammar
    python translate_llm.py --label qwen3-32b --no-grammar --no-repeat-penalty
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

from backend import config as backend_config  # noqa: E402

# LlamaServerEngine.translate() reads config.LLAMA_SERVER_TIMEOUT_S (15s) as a
# literal per-request httpx timeout on every non-fast call, regardless of what
# timeout_s the engine was constructed with — that 15s is tuned for the live
# app's real-time latency budget, not for offline benchmarking. Grammar-
# constrained decoding on a cold/loaded server can legitimately exceed it,
# which otherwise silently turns into empty hyp_ko + a swallowed exception
# (translate_llm.py's own try/except) rather than a real translation — found
# live during this survey's first qwen2.5-7b-baseline run (segments 78-79+
# all came back empty). Raised here, before importing the engine module, so
# every translate() call in this script gets the raised budget.
backend_config.LLAMA_SERVER_TIMEOUT_S = 90.0

from backend.translation.llama_server_engine import LlamaServerEngine  # noqa: E402
from common import load_dataset  # noqa: E402

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True, help="out/<label>/ subdir name")
    parser.add_argument("--no-grammar", action="store_true", help="disable the Korean-only GBNF grammar mask")
    parser.add_argument("--no-repeat-penalty", action="store_true")
    parser.add_argument("--no-think-hint", action="store_true", help="prepend /no_think to the glossary_hint slot (Qwen3 switch)")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    use_grammar = not args.no_grammar
    use_repeat_penalty = not args.no_repeat_penalty
    hint = "/no_think" if args.no_think_hint else None

    out_dir = OUT_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "translations.jsonl"

    mt = LlamaServerEngine(timeout_s=120.0)
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            t_start = time.monotonic()
            try:
                result = await mt.translate(
                    seg.ja_ref,
                    fast=False,
                    glossary_hint=hint,
                    use_grammar=use_grammar,
                    use_repeat_penalty=use_repeat_penalty,
                )
                hyp = result.text
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
    await mt.aclose()

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
