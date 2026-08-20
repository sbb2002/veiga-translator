"""Run the STT+translation pipeline over data/eval_set_*.jsonl and score it
per docs/eval/EVAL.md (final pass only — see note below).

Scope (ponytail: cut for a first pass, see docs/eval/EVAL.md for the full spec):
- Only the FINAL pass is run (beam=5 STT, natural-translation prompt), not
  partial. Partial scoring (EVAL.md §3.3) needs simulated partial
  audio/text truncation to be meaningful; that's a separate follow-up.
- Automated metrics only: CER (§2.2) and chrF++ (§3.1), plus the Latin-script
  auto-fail check (§3.2). The human rubric (meaning/naturalness/honorific
  fidelity, S1 causes, error-category tagging) still needs manual grading —
  this script writes per-segment hypotheses to a jsonl for that pass.

Usage (llama-server must already be running):
    python scripts/run_eval.py data/eval_set_2026-08-18.jsonl
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jiwer
import sacrebleu

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.stt.faster_whisper_engine import FasterWhisperEngine  # noqa: E402
from backend.translation.llama_server_engine import LlamaServerEngine  # noqa: E402

_JA_PUNCT_RE = re.compile(r"[、。！？「」『』・…\s]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def normalize_ja(text: str) -> str:
    """EVAL.md §2.1: NFKC, strip listed punctuation + whitespace."""
    return _JA_PUNCT_RE.sub("", unicodedata.normalize("NFKC", text))


async def main() -> None:
    eval_path = Path(sys.argv[1]) if len(sys.argv) > 1 else sorted(ROOT.glob("data/eval_set_*.jsonl"))[-1]
    records = [json.loads(line) for line in eval_path.read_text(encoding="utf-8").splitlines()]
    print(f"Loaded {len(records)} segments from {eval_path.name}")

    # CPU override: config.py is pinned to CUDA for the real-time backend.
    stt = FasterWhisperEngine(device="cpu", compute_type="int8")
    mt = LlamaServerEngine(timeout_s=120.0)  # CPU inference is much slower than the GPU-tuned default

    results = []
    for i, r in enumerate(records):
        wav_path = str(ROOT / r["audio"])
        stt_result = stt.transcribe(wav_path, fast=False)
        hyp_ja = stt_result.text
        mt_result = await mt.translate(hyp_ja, fast=False)
        hyp_ko = mt_result.text

        results.append({**r, "hyp_ja": hyp_ja, "hyp_ko": hyp_ko})
        print(f"[{i + 1}/{len(records)}] {r['id']}: {hyp_ja[:30]!r} -> {hyp_ko[:30]!r}")

    await mt.aclose()

    out_path = eval_path.with_name(eval_path.stem + "_results.jsonl")
    with out_path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nWrote hypotheses -> {out_path}")

    report(results)


def report(results: list[dict]) -> None:
    groups = ["normal", "hard", "all"]
    print("\n=== STT (final CER) ===")
    print(f"{'group':<8}{'n':<5}{'CER':<8}{'proper_noun_CER'}")
    for g in groups:
        rows = [r for r in results if g == "all" or r["group"] == g]
        if not rows:
            print(f"{g:<8}{0:<5}{'n/a':<8}n/a")
            continue
        cer = jiwer.cer(
            [normalize_ja(r["ja_ref"]) for r in rows],
            [normalize_ja(r["hyp_ja"]) for r in rows],
        )
        pn_rows = [r for r in rows if r["has_proper_noun"]]
        pn_cer = (
            jiwer.cer(
                [normalize_ja(r["ja_ref"]) for r in pn_rows],
                [normalize_ja(r["hyp_ja"]) for r in pn_rows],
            )
            if pn_rows
            else float("nan")
        )
        print(f"{g:<8}{len(rows):<5}{cer:<8.4f}{pn_cer:.4f} (n={len(pn_rows)})")

    print("\n=== Translation (final chrF++, Latin-script S1 auto-fail) ===")
    print(f"{'group':<8}{'n':<5}{'chrF++':<10}{'latin_leak_count'}")
    for g in groups:
        rows = [r for r in results if g == "all" or r["group"] == g]
        if not rows:
            print(f"{g:<8}{0:<5}{'n/a':<10}0")
            continue
        chrf = sacrebleu.corpus_chrf(
            [r["hyp_ko"] for r in rows],
            [[r["ko_ref"] for r in rows]],
            word_order=2,
        ).score
        latin_leaks = sum(1 for r in rows if _LATIN_RE.search(r["hyp_ko"]))
        print(f"{g:<8}{len(rows):<5}{chrf:<10.2f}{latin_leaks}")

    print(
        "\nNote: 의미 충실도/자연스러움/존댓말 일치, S1 원인 분리(§3.4)는 "
        "*_results.jsonl을 사람이 채점해야 함 (docs/eval/EVAL.md §3.2, §5)."
    )


if __name__ == "__main__":
    asyncio.run(main())
