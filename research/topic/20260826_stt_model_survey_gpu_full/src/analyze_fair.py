"""Fairness re-run analysis (report/03-fairness-review.md): compares the
ReazonSpeech FAIR re-run (official reazonspeech wrapper, CPU) against the
original bare-NeMo run and against turbo / Qwen3-ASR-1.7B.

Same methodology as analyze_ci_and_plot.py — paired bootstrap, 500 resamples,
SEED=42, corpus metrics recomputed each resample — so the turbo / qwen CIs
here reproduce report/01 exactly. Adds paired-difference CIs (fair - X) so we
can test whether the gap the original run showed actually closes.

RTF note: reazonspeech-nemo-v2_fair was transcribed on CPU. Its RTF is NOT
comparable to the GPU RTF of the other methods (report/01). Shown for
completeness, flagged in the table.

Usage: python analyze_fair.py
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

plt.rcParams["font.family"] = ["Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

from common import CATEGORIES, normalize_ja
from score_quantitative import rouge_l_f1

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
FIG_ROOT = TOPIC_ROOT / "fig"

METHODS = ["turbo", "qwen3-asr-1.7b", "reazonspeech-nemo-v2", "reazonspeech-nemo-v2_fair"]
LABELS = {
    "turbo": "large-v3-turbo (GPU)",
    "qwen3-asr-1.7b": "Qwen3-ASR-1.7B (GPU)",
    "reazonspeech-nemo-v2": "ReazonSpeech bare (GPU)",
    "reazonspeech-nemo-v2_fair": "ReazonSpeech fair/wrapper (CPU)",
}
DIFF_PAIRS = [
    ("reazonspeech-nemo-v2_fair", "reazonspeech-nemo-v2"),  # did the wrapper help?
    ("reazonspeech-nemo-v2_fair", "turbo"),
    ("reazonspeech-nemo-v2_fair", "qwen3-asr-1.7b"),
]
N_BOOT = 500
SEED = 42
METRIC_KEYS = ["cer", "chrf++", "bleu_char", "rouge_l_f1"]
METRIC_TITLES = {"cer": "CER (↓ better)", "chrf++": "chrF++ (↑)", "bleu_char": "BLEU-char (↑)", "rouge_l_f1": "ROUGE-L F1 (↑)"}
LOWER_BETTER = {"cer"}


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


def _pct(vals: list[float], p: float) -> float:
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(p * len(vals)))]


def bootstrap_single(rows: list[dict], seed: int) -> dict:
    rng = random.Random(seed)
    n = len(rows)
    point = corpus_metrics(rows)
    samples = {k: [] for k in point}
    for _ in range(N_BOOT):
        resample = [rows[rng.randrange(n)] for _ in range(n)]
        for k, v in corpus_metrics(resample).items():
            samples[k].append(v)
    out = {}
    for k, vals in samples.items():
        lo, hi = _pct(vals, 0.025), _pct(vals, 0.975)
        out[k] = {"point": point[k], "ci_lo": lo, "ci_hi": hi, "hw": (hi - lo) / 2}
    return out


def bootstrap_paired_diff(rows_a: list[dict], rows_b: list[dict], seed: int) -> dict:
    """CI of metric(A) - metric(B) with the SAME resampled indices for both
    (paired). rows_a / rows_b must be aligned by seg_id."""
    assert [r["seg_id"] for r in rows_a] == [r["seg_id"] for r in rows_b], "rows not aligned by seg_id"
    rng = random.Random(seed)
    n = len(rows_a)
    pa, pb = corpus_metrics(rows_a), corpus_metrics(rows_b)
    point = {k: pa[k] - pb[k] for k in pa}
    samples = {k: [] for k in pa}
    for _ in range(N_BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        ma = corpus_metrics([rows_a[i] for i in idx])
        mb = corpus_metrics([rows_b[i] for i in idx])
        for k in pa:
            samples[k].append(ma[k] - mb[k])
    out = {}
    for k, vals in samples.items():
        lo, hi = _pct(vals, 0.025), _pct(vals, 0.975)
        out[k] = {"point": point[k], "ci_lo": lo, "ci_hi": hi, "crosses_zero": lo <= 0 <= hi}
    return out


def fmt(v: float, key: str) -> str:
    return f"{v:.3f}" if key in ("cer", "rouge_l_f1") else f"{v:.2f}"


def main() -> None:
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    rows = {m: load_rows(m) for m in METHODS}
    for m in METHODS:
        assert len(rows[m]) == 150, f"{m}: {len(rows[m])} rows"
    ref_ids = [r["seg_id"] for r in rows[METHODS[0]]]
    for m in METHODS[1:]:
        assert [r["seg_id"] for r in rows[m]] == ref_ids, f"{m} seg_id order differs"

    overall = {m: bootstrap_single(rows[m], SEED) for m in METHODS}
    cat_ci = {
        m: {c: bootstrap_single([r for r in rows[m] if r["category"] == c], SEED) for c in CATEGORIES}
        for m in METHODS
    }
    diffs = {
        f"{a} - {b}": bootstrap_paired_diff(rows[a], rows[b], SEED) for a, b in DIFF_PAIRS
    }

    summary = {
        "n_boot": N_BOOT, "seed": SEED,
        "overall": overall, "by_category_cer": {m: {c: cat_ci[m][c]["cer"] for c in CATEGORIES} for m in METHODS},
        "paired_diff": diffs,
    }
    sp = OUT_ROOT / "fair_summary.json"
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- figure 1: 4 metric subplots, 4 methods, 95% CI error bars ---
    fig, axes = plt.subplots(1, 4, figsize=(17, 4.5))
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    for ax, key in zip(axes, METRIC_KEYS):
        vals = [overall[m][key]["point"] for m in METHODS]
        errs = [overall[m][key]["hw"] for m in METHODS]
        ax.bar(range(len(METHODS)), vals, yerr=errs, capsize=4, color=colors[: len(METHODS)])
        ax.set_xticks(range(len(METHODS)))
        ax.set_xticklabels([LABELS[m] for m in METHODS], rotation=30, ha="right", fontsize=7)
        ax.set_title(METRIC_TITLES[key])
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("ReazonSpeech 공정 재실행 — 전체 150쌍, 95% CI (paired bootstrap, 500회)")
    fig.tight_layout()
    fig.savefig(FIG_ROOT / "fair_reazonspeech.png", dpi=150)
    plt.close(fig)

    # --- figure 2: per-category CER, 4 methods ---
    fig, ax = plt.subplots(figsize=(10, 4.5))
    width = 0.8 / len(METHODS)
    for i, m in enumerate(METHODS):
        offset = (i - (len(METHODS) - 1) / 2) * width
        vals = [cat_ci[m][c]["cer"]["point"] for c in CATEGORIES]
        errs = [cat_ci[m][c]["cer"]["hw"] for c in CATEGORIES]
        ax.bar([x + offset for x in range(len(CATEGORIES))], vals, width=width, yerr=errs, capsize=2, label=LABELS[m])
    ax.set_xticks(range(len(CATEGORIES)))
    ax.set_xticklabels(CATEGORIES)
    ax.set_ylabel("CER (↓ better)")
    ax.set_title("카테고리별 CER — 95% CI (n=30)")
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIG_ROOT / "fair_reazonspeech_category_cer.png", dpi=150)
    plt.close(fig)

    # --- markdown ---
    print("### 전체 (150쌍, 95% CI = paired bootstrap 500회, SEED=42)\n")
    print("| 방법 | CER↓ | chrF++↑ | BLEU-char↑ | ROUGE-L F1↑ | RTF↓ |")
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        c = overall[m]
        rtf = f"{c['rtf']['point']:.3f} ± {c['rtf']['hw']:.3f}"
        if m == "reazonspeech-nemo-v2_fair":
            rtf += " *(CPU)*"
        print(f"| {LABELS[m]} | {fmt(c['cer']['point'],'cer')} ± {fmt(c['cer']['hw'],'cer')} "
              f"| {fmt(c['chrf++']['point'],'chrf++')} ± {fmt(c['chrf++']['hw'],'chrf++')} "
              f"| {fmt(c['bleu_char']['point'],'bleu_char')} ± {fmt(c['bleu_char']['hw'],'bleu_char')} "
              f"| {fmt(c['rouge_l_f1']['point'],'rouge_l_f1')} ± {fmt(c['rouge_l_f1']['hw'],'rouge_l_f1')} | {rtf} |")
    print("\n\\* RTF: fair 런은 CPU 측정 — report/01의 GPU RTF와 비교 불가.\n")

    print("### 페어드 차이 (A − B), 95% CI — CI가 0을 포함하면 '차이 없음'\n")
    print("| 비교 (A − B) | CER | chrF++ | BLEU-char | ROUGE-L F1 |")
    print("|---|---|---|---|---|")
    for name, d in diffs.items():
        cells = []
        for key in METRIC_KEYS:
            e = d[key]
            mark = " (0 포함)" if e["crosses_zero"] else ""
            cells.append(f"{fmt(e['point'],key)} [{fmt(e['ci_lo'],key)}, {fmt(e['ci_hi'],key)}]{mark}")
        print(f"| {name} | " + " | ".join(cells) + " |")

    print("\n### 카테고리별 CER (95% CI, n=30)\n")
    print("| 카테고리 | " + " | ".join(LABELS[m] for m in METHODS) + " |")
    print("|---|" + "---|" * len(METHODS))
    for c in CATEGORIES:
        cells = [f"{cat_ci[m][c]['cer']['point']:.3f} ± {cat_ci[m][c]['cer']['hw']:.3f}" for m in METHODS]
        print(f"| {c} | " + " | ".join(cells) + " |")

    print(f"\nsummary -> {sp}\nfigs -> {FIG_ROOT / 'fair_reazonspeech.png'}, {FIG_ROOT / 'fair_reazonspeech_category_cer.png'}")


if __name__ == "__main__":
    main()
