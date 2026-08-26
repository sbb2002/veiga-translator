"""Paired bootstrap 95% CI (same methodology as 20260822_stt_transcription_eval:
500 resamples, corpus-level metrics recomputed from scratch each resample, not
a mean of per-segment values) for the 4 pilot methods' out/<method>/
quant_results.csv, plus bar-chart PNGs with error bars into fig/.

All 4 methods share the same 25 segments (same seg_id order), so per-segment
category CI is also paired.

Usage: python analyze_ci_and_plot.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

import jiwer
import matplotlib.pyplot as plt
import sacrebleu

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False
from common import CATEGORIES, normalize_ja
from score_quantitative import rouge_l_f1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
FIG_ROOT = TOPIC_ROOT / "fig"

METHODS = ["turbo", "granite-speech-4.1-2b", "qwen3-asr-0.6b", "qwen3-asr-1.7b"]
LABELS = {
    "turbo": "large-v3-turbo",
    "granite-speech-4.1-2b": "granite-speech-4.1-2b",
    "qwen3-asr-0.6b": "Qwen3-ASR-0.6B",
    "qwen3-asr-1.7b": "Qwen3-ASR-1.7B",
}
N_BOOT = 500
SEED = 42
METRIC_KEYS = ["cer", "chrf++", "bleu_char", "rouge_l_f1"]


def load_rows(method: str) -> list[dict]:
    path = OUT_ROOT / method / "quant_results.csv"
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def corpus_metrics(rows: list[dict]) -> dict:
    refs = [normalize_ja(r["ja_ref"]) for r in rows]
    hyps = [normalize_ja(r["hyp"]) for r in rows]
    cer = jiwer.cer(refs, hyps)
    chrf = sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score
    bleu = sacrebleu.corpus_bleu(hyps, [refs], tokenize="char").score
    rouge = sum(rouge_l_f1(r, h) for r, h in zip(refs, hyps)) / len(rows)
    total_audio = sum(float(r["duration_s"]) for r in rows)
    total_stt = sum(float(r["stt_elapsed_s"]) for r in rows)
    return {"cer": cer, "chrf++": chrf, "bleu_char": bleu, "rouge_l_f1": rouge, "rtf": total_stt / total_audio}


def bootstrap_ci(rows: list[dict], seed: int) -> dict:
    rng = random.Random(seed)
    n = len(rows)
    point = corpus_metrics(rows)
    samples = {k: [] for k in point}
    for _ in range(N_BOOT):
        resample = [rows[rng.randrange(n)] for _ in range(n)]
        m = corpus_metrics(resample)
        for k, v in m.items():
            samples[k].append(v)
    result = {}
    for k, vals in samples.items():
        vals.sort()
        lo = vals[int(0.025 * N_BOOT)]
        hi = vals[int(0.975 * N_BOOT)]
        result[k] = {"point": point[k], "ci_lo": lo, "ci_hi": hi, "ci_halfwidth": (hi - lo) / 2}
    return result


def bar_chart(path: Path, title: str, group_labels: list[str], series: dict[str, tuple[list[float], list[float]]], ylabel: str) -> None:
    """series: {method_label: (values, ci_halfwidths)} aligned to group_labels."""
    n_groups = len(group_labels)
    n_series = len(series)
    width = 0.8 / n_series
    fig, ax = plt.subplots(figsize=(max(6, n_groups * 1.6), 4.5))
    x = range(n_groups)
    for i, (label, (vals, errs)) in enumerate(series.items()):
        offset = (i - (n_series - 1) / 2) * width
        xs = [xi + offset for xi in x]
        ax.bar(xs, vals, width=width, yerr=errs, capsize=3, label=label)
    ax.set_xticks(list(x))
    ax.set_xticklabels(group_labels)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    all_rows = {m: load_rows(m) for m in METHODS}

    overall_ci = {m: bootstrap_ci(rows, seed=SEED) for m, rows in all_rows.items()}

    category_ci: dict[str, dict[str, dict]] = {m: {} for m in METHODS}
    for m, rows in all_rows.items():
        for cat in CATEGORIES:
            cat_rows = [r for r in rows if r["category"] == cat]
            category_ci[m][cat] = bootstrap_ci(cat_rows, seed=SEED)

    summary = {"overall": overall_ci, "by_category": category_ci}
    summary_path = OUT_ROOT / "ci_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # quant_metrics.png: 4 metrics x 4 methods
    series = {}
    for m in METHODS:
        vals = [overall_ci[m][k]["point"] for k in METRIC_KEYS]
        errs = [overall_ci[m][k]["ci_halfwidth"] for k in METRIC_KEYS]
        series[LABELS[m]] = (vals, errs)
    bar_chart(
        FIG_ROOT / "quant_metrics.png",
        "CPU 파일럿(25세그먼트) 정량 지표 — 95% CI",
        ["CER", "chrF++", "BLEU(char)", "ROUGE-L F1"],
        series,
        "score",
    )

    # rtf.png
    series = {LABELS[m]: ([overall_ci[m]["rtf"]["point"]], [overall_ci[m]["rtf"]["ci_halfwidth"]]) for m in METHODS}
    bar_chart(FIG_ROOT / "rtf.png", "RTF (CPU) — 95% CI, 낮을수록 빠름", ["RTF"], series, "RTF")

    # category_cer.png
    series = {}
    for m in METHODS:
        vals = [category_ci[m][cat]["cer"]["point"] for cat in CATEGORIES]
        errs = [category_ci[m][cat]["cer"]["ci_halfwidth"] for cat in CATEGORIES]
        series[LABELS[m]] = (vals, errs)
    bar_chart(FIG_ROOT / "category_cer.png", "카테고리별 CER — 95% CI (n=5)", CATEGORIES, series, "CER")

    # print markdown-ready tables
    print("### 전체 (95% CI)\n")
    header = "| 방법 | CER↓ | chrF++↑ | BLEU(char)↑ | ROUGE-L F1↑ | RTF↓ |"
    print(header)
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        c = overall_ci[m]
        row = f"| {LABELS[m]} | {c['cer']['point']:.3f} ± {c['cer']['ci_halfwidth']:.3f} | {c['chrf++']['point']:.2f} ± {c['chrf++']['ci_halfwidth']:.2f} | {c['bleu_char']['point']:.2f} ± {c['bleu_char']['ci_halfwidth']:.2f} | {c['rouge_l_f1']['point']:.3f} ± {c['rouge_l_f1']['ci_halfwidth']:.3f} | {c['rtf']['point']:.3f} ± {c['rtf']['ci_halfwidth']:.3f} |"
        print(row)

    print("\n### 카테고리별 CER (95% CI, n=5)\n")
    print("| 카테고리 | " + " | ".join(LABELS[m] for m in METHODS) + " |")
    print("|---|" + "---|" * len(METHODS))
    for cat in CATEGORIES:
        cells = [f"{category_ci[m][cat]['cer']['point']:.3f} ± {category_ci[m][cat]['cer']['ci_halfwidth']:.3f}" for m in METHODS]
        print(f"| {cat} | " + " | ".join(cells) + " |")

    print(f"\nsummary -> {summary_path}\nfigs -> {FIG_ROOT}")


if __name__ == "__main__":
    main()
