"""Score VAD-STT pipeline transcripts against ja_ref with quantitative metrics.

Input: JSONL transcript files (A=pipeline, B=baseline).
Output: per-segment JSONL + corpus summary JSON + printed summary.

Metrics: CER (jiwer), chrF++ (word_order=2), BLEU-char, ROUGE-L F1.
Normalized with normalize_ja (NFKC + punctuation strip, shared with baseline).

Usage:
  python score_quantitative.py --run A_turbo --transcripts path/to/pipeline_transcripts.jsonl
  python score_quantitative.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jiwer
import sacrebleu

from common import normalize_ja

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"


def lcs_length(a: str, b: str) -> int:
    """Longest common subsequence length (for ROUGE-L)."""
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
    """ROUGE-L F1 score."""
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


def score_segment(ref_norm: str, hyp_norm: str) -> dict:
    """Score a single segment. Return metrics dict with rounded values."""
    cer = jiwer.cer(ref_norm, hyp_norm) if ref_norm else (0.0 if not hyp_norm else 1.0)
    chrf = sacrebleu.sentence_chrf(hyp_norm, [ref_norm], word_order=2).score
    bleu = sacrebleu.sentence_bleu(hyp_norm, [ref_norm], tokenize="char").score
    rouge = rouge_l_f1(ref_norm, hyp_norm)

    return {
        "cer": round(cer, 4),
        "chrf++": round(chrf, 2),
        "bleu_char": round(bleu, 2),
        "rouge_l_f1": round(rouge, 4),
    }


def score_group(refs_norm: list[str], hyps_norm: list[str]) -> dict:
    """Score a group (category or overall). refs/hyps are already normalized strings."""
    cer = jiwer.cer(refs_norm, hyps_norm)
    chrf = sacrebleu.corpus_chrf(hyps_norm, [refs_norm], word_order=2).score
    bleu = sacrebleu.corpus_bleu(hyps_norm, [refs_norm], tokenize="char").score
    rouge = sum(rouge_l_f1(r, h) for r, h in zip(refs_norm, hyps_norm)) / len(refs_norm)

    return {
        "n": len(refs_norm),
        "cer": cer,
        "chrf++": chrf,
        "bleu_char": bleu,
        "rouge_l_f1": rouge,
    }


def run_check() -> None:
    """Validate metric functions with synthetic data."""
    # identical
    ref_norm = "テスト"
    hyp_norm = "テスト"
    metrics = score_segment(ref_norm, hyp_norm)
    assert metrics["cer"] == 0.0, f"identical ref==hyp should cer=0, got {metrics['cer']}"
    assert metrics["chrf++"] >= 99, f"identical should chrf~100, got {metrics['chrf++']}"
    assert metrics["rouge_l_f1"] == 1.0, f"identical should rouge=1, got {metrics['rouge_l_f1']}"

    # empty hyp, non-empty ref
    ref_norm = "テスト"
    hyp_norm = ""
    metrics = score_segment(ref_norm, hyp_norm)
    assert metrics["cer"] == 1.0, f"empty hyp should cer=1, got {metrics['cer']}"
    assert metrics["rouge_l_f1"] == 0.0, f"empty hyp should rouge=0, got {metrics['rouge_l_f1']}"

    # both empty
    ref_norm = ""
    hyp_norm = ""
    metrics = score_segment(ref_norm, hyp_norm)
    assert metrics["rouge_l_f1"] == 1.0, f"both empty should rouge=1, got {metrics['rouge_l_f1']}"

    # partial overlap
    ref_norm = "こんにちは"
    hyp_norm = "こんにちは世界"
    metrics = score_segment(ref_norm, hyp_norm)
    assert 0 < metrics["cer"] < 1, f"partial should have 0 < cer < 1, got {metrics['cer']}"
    assert 0 < metrics["rouge_l_f1"] < 1, f"partial should have 0 < rouge < 1, got {metrics['rouge_l_f1']}"

    # corpus-level
    refs_norm = ["テスト", "こんにちは"]
    hyps_norm = ["テスト", "こんにちは"]
    summary = score_group(refs_norm, hyps_norm)
    assert summary["cer"] == 0.0, f"corpus identical should cer=0, got {summary['cer']}"
    assert summary["chrf++"] >= 99, f"corpus identical should chrf~100, got {summary['chrf++']}"

    # range checks (allow small fp precision error)
    for metric in ["cer", "rouge_l_f1"]:
        assert -0.001 <= summary[metric] <= 1.001, f"{metric} should be in [0,1], got {summary[metric]}"
    for metric in ["chrf++", "bleu_char"]:
        assert -0.1 <= summary[metric] <= 100.1, f"{metric} should be in [0,100], got {summary[metric]}"

    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score VAD-STT pipeline transcripts."
    )
    parser.add_argument("--run", help="Output folder label (e.g., A_turbo)")
    parser.add_argument("--transcripts", type=Path, help="Path to JSONL transcripts")
    parser.add_argument("--check", action="store_true", help="Run validation check and exit")
    args = parser.parse_args()

    if args.check:
        run_check()
        return

    if not args.run or not args.transcripts:
        parser.error("--run and --transcripts are required (or use --check)")

    # Load transcripts
    records = []
    with args.transcripts.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))

    # Per-segment scoring: keep normalized strings for re-computation by analyze_stats.py
    per_seg_rows = []
    for r in records:
        ref_norm = normalize_ja(r["ja_ref"])
        hyp_norm = normalize_ja(r["hyp"])
        metrics = score_segment(ref_norm, hyp_norm)

        per_seg_rows.append({
            "seg_id": r["seg_id"],
            "category": r["category"],
            "duration_s": r["duration_s"],
            "ref_norm": ref_norm,  # keep as string, not rounded
            "hyp_norm": hyp_norm,  # keep as string, not rounded
            **metrics,
            "stt_elapsed_s": r.get("stt_elapsed_s"),
        })

    # Prepare output directory
    out_dir = OUT_ROOT / args.run
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write per-segment JSONL
    per_seg_path = out_dir / "quant_per_segment.jsonl"
    with per_seg_path.open("w", encoding="utf-8") as f:
        for row in per_seg_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Corpus summary: per-category + overall
    all_refs_norm = [r["ref_norm"] for r in per_seg_rows]
    all_hyps_norm = [r["hyp_norm"] for r in per_seg_rows]

    summary = {
        "overall": score_group(all_refs_norm, all_hyps_norm),
    }

    # Per-category
    categories = sorted(set(r["category"] for r in records))
    for cat in categories:
        cat_rows = [row for row in per_seg_rows if row["category"] == cat]
        cat_refs = [row["ref_norm"] for row in cat_rows]
        cat_hyps = [row["hyp_norm"] for row in cat_rows]
        summary[cat] = score_group(cat_refs, cat_hyps)

    # RTF: sum(stt_elapsed_s) / sum(duration_s)
    total_audio_s = sum(r["duration_s"] for r in records)
    total_stt_s = sum(r.get("stt_elapsed_s") or 0.0 for r in records)

    # Count missing stt_elapsed_s for notation
    stt_missing_count = sum(1 for r in records if r.get("stt_elapsed_s") is None)

    summary["rtf"] = total_stt_s / total_audio_s if total_audio_s > 0 else 0.0
    summary["total_stt_s"] = total_stt_s
    summary["total_audio_s"] = total_audio_s
    if stt_missing_count > 0:
        summary["_stt_missing"] = stt_missing_count

    # Write summary JSON
    summary_path = out_dir / "quant_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Print summary to stdout + written paths
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nwrote -> {per_seg_path}, {summary_path}")


if __name__ == "__main__":
    main()
