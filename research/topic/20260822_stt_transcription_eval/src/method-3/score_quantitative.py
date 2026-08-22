"""Score out/transcripts.jsonl against ja_ref with multiple quantitative
metrics, per-category and overall, and write out/quant_results.csv +
out/quant_summary.json.

Metrics (all computed on the SAME normalized text — NFKC + punctuation/
whitespace stripped, per docs/eval/EVAL.md §2.1 — for apples-to-apples
comparison across metrics):

- CER   : character error rate (jiwer), EVAL.md's existing STT standard.
- chrF++: sacrebleu corpus/sentence chrf, word_order=2 (character n-gram,
          MT-standard, tokenization-free — works for Japanese unsegmented text).
- BLEU  : sacrebleu BLEU with tokenize="char" — Japanese has no whitespace
          word boundaries, so char-level tokenization is the standard way to
          apply BLEU to CJK text without a separate morphological tokenizer
          (avoids adding a MeCab/fugashi dependency for this one metric).
- ROUGE-L: character-level LCS-based F1 (recall-oriented, complements BLEU's
          precision-orientation) — implemented manually (no rouge_score
          dependency) since it's a ~15-line algorithm.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import jiwer
import sacrebleu
from common import normalize_ja

OUT_DIR = Path(__file__).resolve().parents[2] / "out" / "method-3"
TRANSCRIPTS_PATH = OUT_DIR / "transcripts.jsonl"
OUT_CSV = OUT_DIR / "quant_results.csv"
OUT_SUMMARY = OUT_DIR / "quant_summary.json"


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


def load_records() -> list[dict]:
    records = []
    with TRANSCRIPTS_PATH.open(encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


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
    records = load_records()

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
                "no_speech_prob": r["no_speech_prob"],
                "avg_logprob": r["avg_logprob"],
            }
        )

    OUT_CSV.parent.mkdir(exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_seg_rows[0].keys()))
        writer.writeheader()
        writer.writerows(per_seg_rows)

    categories = sorted({r["category"] for r in records})
    summary = {"overall": score_group(records)}
    for cat in categories:
        cat_records = [r for r in records if r["category"] == cat]
        summary[cat] = score_group(cat_records)

    with OUT_SUMMARY.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-segment -> {OUT_CSV}\nsummary -> {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
