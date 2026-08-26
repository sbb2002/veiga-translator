"""Score out/<method>/translations.jsonl's hyp_ko against ko_ref with chrF++
(docs/eval/EVAL.md §3.1: sacrebleu.corpus_chrf(hyp, [ref], word_order=2), no
tokenization/normalization needed — chrF++ is character-n-gram based).

Also flags EVAL.md §3.2's automatic S1 script-purity failure: any Latin
letter (A-Z/a-z) in the output. Shared across all methods via --method
(out/<method>/ subdir), same convention as the STT surveys.

Usage: python score_chrf.py --method gemma-3-12b-it
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import sacrebleu

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
_LATIN_RE = re.compile(r"[A-Za-z]")


def score_group(records: list[dict]) -> dict:
    refs = [r["ko_ref"] for r in records]
    hyps = [r["hyp_ko"] for r in records]
    chrf = sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score
    latin_leak = sum(1 for h in hyps if _LATIN_RE.search(h))
    return {"n": len(records), "chrf++": chrf, "latin_leak_n": latin_leak}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", required=True, help="out/<method>/ subdir name")
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.method
    records = [json.loads(line) for line in (out_dir / "translations.jsonl").open(encoding="utf-8")]

    per_seg_rows = []
    for r in records:
        seg_chrf = sacrebleu.sentence_chrf(r["hyp_ko"], [r["ko_ref"]], word_order=2).score
        per_seg_rows.append(
            {
                "seg_id": r["seg_id"],
                "category": r["category"],
                "group": r["group"],
                "has_proper_noun": r["has_proper_noun"],
                "duration_s": r["duration_s"],
                "ko_ref": r["ko_ref"],
                "hyp_ko": r["hyp_ko"],
                "chrf++": round(seg_chrf, 2),
                "latin_leak": bool(_LATIN_RE.search(r["hyp_ko"])),
                "translate_elapsed_s": r.get("translate_elapsed_s"),
            }
        )

    out_csv = out_dir / "chrf_results.csv"
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_seg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_seg_rows)

    groups = sorted({r["group"] for r in records})
    summary = {"overall": score_group(records)}
    for g in groups:
        summary[g] = score_group([r for r in records if r["group"] == g])

    total_audio_s = sum(r["duration_s"] for r in records)
    total_translate_s = sum(r["translate_elapsed_s"] for r in records)
    summary["rtf"] = total_translate_s / total_audio_s
    summary["total_translate_s"] = total_translate_s
    summary["total_audio_s"] = total_audio_s

    out_summary = out_dir / "chrf_summary.json"
    with out_summary.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-segment -> {out_csv}\nsummary -> {out_summary}")


if __name__ == "__main__":
    main()
