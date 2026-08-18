"""EVAL.md §3.4 STT-propagation check, done as a corpus-level decomposition
instead of per-segment manual tagging: translate ja_ref directly (bypassing
STT entirely) and compare chrF++ against translating the STT hypothesis.
If ref-translation chrF++ >> hyp-translation chrF++, STT errors are the
dominant cause of bad final translations; if they're close, the translation
engine itself is the bottleneck.

Usage (llama-server must be running):
    python scripts/eval_stt_propagation.py data/eval_set_2026-08-18_results.jsonl
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import sacrebleu

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from backend.translation.llama_server_engine import LlamaServerEngine  # noqa: E402


async def main() -> None:
    in_path = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(ROOT.glob("data/eval_set_*_results.jsonl"))[-1]
    records = [json.loads(line) for line in in_path.read_text(encoding="utf-8").splitlines()]
    print(f"Loaded {len(records)} segments from {in_path.name}")

    mt = LlamaServerEngine(timeout_s=120.0)
    for i, r in enumerate(records):
        result = await mt.translate(r["ja_ref"], fast=False)
        r["hyp_ko_from_ref"] = result.text
        print(f"[{i + 1}/{len(records)}] {r['id']}")
    await mt.aclose()

    out_path = in_path.with_name(in_path.stem + "_refmt.jsonl")
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote -> {out_path}")

    groups = ["normal", "hard", "all"]
    print("\n=== chrF++: translate(STT hyp) vs translate(ja_ref) ===")
    print(f"{'group':<8}{'n':<5}{'hyp_ja->ko':<12}{'ref_ja->ko':<12}{'gap'}")
    for g in groups:
        rows = [r for r in records if g == "all" or r["group"] == g]
        chrf_hyp = sacrebleu.corpus_chrf(
            [r["hyp_ko"] for r in rows], [[r["ko_ref"] for r in rows]], word_order=2
        ).score
        chrf_ref = sacrebleu.corpus_chrf(
            [r["hyp_ko_from_ref"] for r in rows], [[r["ko_ref"] for r in rows]], word_order=2
        ).score
        print(f"{g:<8}{len(rows):<5}{chrf_hyp:<12.2f}{chrf_ref:<12.2f}{chrf_ref - chrf_hyp:+.2f}")


if __name__ == "__main__":
    asyncio.run(main())
