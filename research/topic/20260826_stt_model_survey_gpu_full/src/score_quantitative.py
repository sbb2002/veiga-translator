"""Score out/<method>/transcripts.jsonl against ja_ref with the same metrics
as 20260822_stt_transcription_eval / 20260826_stt_model_survey (CER/chrF++/
BLEU-char/ROUGE-L), per-category and overall. Shared across all methods in
this topic via --method (out/ subdir).

Usage: python score_quantitative.py --method turbo
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jiwer
import sacrebleu
from common import normalize_ja

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"


def lcs_length(a: str, b: str) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            cur[j] = prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1])
        prev = cur
    return prev[-1]


def rouge_l_f1(ref: str, hyp: str) -> float:
    if not ref and not hyp:
        return 1.0
    if not ref or not hyp:
        return 0.0
    lcs = lcs_length(ref, hyp)
    recall = lcs / len(ref)
    precision = lcs / len(hyp)
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def score_group(records: list[dict]) -> dict:
    refs_norm = [normalize_ja(r["ja_ref"]) for r in records]
    hyps_norm = [normalize_ja(r["hyp"]) for r in records]

    cer = jiwer.cer(refs_norm, hyps_norm)
    chrf = sacrebleu.corpus_chrf(hyps_norm, [refs_norm], word_order=2).score
    bleu = sacrebleu.corpus_bleu(hyps_norm, [refs_norm], tokenize="char").score
    rouge_l = sum(rouge_l_f1(r, h) for r, h in zip(refs_norm, hyps_norm)) / len(records)

    return {
        "n": len(records),
        "cer": cer,
        "chrf++": chrf,
        "bleu_char": bleu,
        "rouge_l_f1": rouge_l,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", required=True, help="out/<method>/ subdir name")
    args = parser.parse_args()

    out_dir = OUT_ROOT / args.method
    records = [json.loads(line) for line in (out_dir / "transcripts.jsonl").open(encoding="utf-8")]

    per_seg_rows = []
    for r in records:
        ref_n = normalize_ja(r["ja_ref"])
        hyp_n = normalize_ja(r["hyp"])
        seg_cer = jiwer.cer(ref_n, hyp_n) if ref_n else (0.0 if not hyp_n else 1.0)
        seg_chrf = sacrebleu.sentence_chrf(hyp_n, [ref_n], word_order=2).score
        seg_bleu = sacrebleu.sentence_bleu(hyp_n, [ref_n], tokenize="char").score
        seg_rouge = rouge_l_f1(ref_n, hyp_n)
        per_seg_rows.append(
            {
                "seg_id": r["seg_id"],
                "category": r["category"],
                "duration_s": r["duration_s"],
                "ja_ref": r["ja_ref"],
                "hyp": r["hyp"],
                "cer": round(seg_cer, 4),
                "chrf++": round(seg_chrf, 2),
                "bleu_char": round(seg_bleu, 2),
                "rouge_l_f1": round(seg_rouge, 4),
                "stt_elapsed_s": r.get("stt_elapsed_s"),
            }
        )

    out_csv = out_dir / "quant_results.csv"
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_seg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_seg_rows)

    categories = sorted({r["category"] for r in records})
    summary = {"overall": score_group(records)}
    for cat in categories:
        cat_records = [r for r in records if r["category"] == cat]
        summary[cat] = score_group(cat_records)

    total_audio_s = sum(r["duration_s"] for r in records)
    total_stt_s = sum(r["stt_elapsed_s"] for r in records)
    summary["rtf"] = total_stt_s / total_audio_s
    summary["total_stt_s"] = total_stt_s
    summary["total_audio_s"] = total_audio_s

    out_summary = out_dir / "quant_summary.json"
    with out_summary.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-segment -> {out_csv}\nsummary -> {out_summary}")


if __name__ == "__main__":
    main()
