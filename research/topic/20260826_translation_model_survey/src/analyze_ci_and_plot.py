"""Paired bootstrap 95% CI (same methodology as the STT surveys: 500
resamples, corpus-level chrF++ recomputed from scratch each resample, not a
mean of per-segment values) for each method's out/<method>/chrf_results.csv,
plus bar-chart PNGs with error bars into fig/.

Every method is scored against the SAME 120-segment eval set (same seg_id
order), so the comparison is paired.

Usage: python analyze_ci_and_plot.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import sacrebleu

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
FIG_ROOT = TOPIC_ROOT / "fig"

# method dir name -> display label. See README.md for the full roster and
# why each one is in/out (report/01-model-scoping.md has the details).
LABELS: dict[str, str] = {
    "qwen2.5-7b-baseline": "Qwen2.5-7B",
    "gemma-3-12b-it": "Gemma-3-12b-it",
    "exaone-3.5-7.8b": "EXAONE-3.5-7.8B",
    "qwen3-14b": "Qwen3-14B",
    "qwen3-32b": "Qwen3-32B",
    "exaone-4.0-32b": "EXAONE-4.0-32B",
    "seed-x-instruct-7b": "Seed-X-Instruct-7B",
    "llama-3-8b-instruct": "Llama-3-8B",
    "nllb-200-3.3b": "NLLB-200-3.3B",
    "madlad400-3b-mt": "MADLAD-400-3B",
}

N_BOOT = 500
SEED = 42


def load_rows(method: str) -> list[dict]:
    path = OUT_ROOT / method / "chrf_results.csv"
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def corpus_metrics(rows: list[dict]) -> dict:
    refs = [r["ko_ref"] for r in rows]
    hyps = [r["hyp_ko"] for r in rows]
    chrf = sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score
    result = {"chrf++": chrf}
    timed = [r for r in rows if r.get("translate_elapsed_s") not in (None, "", "None")]
    if timed:
        total_audio = sum(float(r["duration_s"]) for r in timed)
        total_translate = sum(float(r["translate_elapsed_s"]) for r in timed)
        result["rtf"] = total_translate / total_audio if total_audio else None
    return result


def bootstrap_ci(rows: list[dict], seed: int) -> dict:
    rng = random.Random(seed)
    n = len(rows)
    point = corpus_metrics(rows)
    samples: dict[str, list[float]] = {k: [] for k in point if point[k] is not None}
    for _ in range(N_BOOT):
        resample = [rows[rng.randrange(n)] for _ in range(n)]
        m = corpus_metrics(resample)
        for k in samples:
            samples[k].append(m[k])
    result = {}
    for k, vals in samples.items():
        vals.sort()
        lo = vals[int(0.025 * N_BOOT)]
        hi = vals[int(0.975 * N_BOOT)]
        result[k] = {"point": point[k], "ci_lo": lo, "ci_hi": hi, "ci_halfwidth": (hi - lo) / 2}
    return result


def bar_chart(path: Path, title: str, labels: list[str], vals: list[float], errs: list[float], ylabel: str) -> None:
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.4), 4.5))
    colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    ax.bar(range(len(labels)), vals, yerr=errs, capsize=3, color=[colors[i % len(colors)] for i in range(len(labels))])
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> None:
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    discovered = {d.name for d in OUT_ROOT.iterdir() if (d / "chrf_results.csv").exists()}
    if not discovered:
        print("no out/<method>/chrf_results.csv found yet — run score_chrf.py first")
        return
    # LABELS order first (readable roster order), then any undeclared extras.
    methods = [m for m in LABELS if m in discovered] + sorted(discovered - set(LABELS))
    labels = {m: LABELS.get(m, m) for m in methods}

    all_rows = {m: load_rows(m) for m in methods}
    overall_ci = {m: bootstrap_ci(rows, seed=SEED) for m, rows in all_rows.items()}

    group_ci: dict[str, dict[str, dict]] = {m: {} for m in methods}
    for m, rows in all_rows.items():
        for g in ["normal", "hard"]:
            g_rows = [r for r in rows if r["group"] == g]
            if g_rows:
                group_ci[m][g] = bootstrap_ci(g_rows, seed=SEED)

    summary = {"overall": overall_ci, "by_group": group_ci}
    summary_path = OUT_ROOT / "ci_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    chrf_vals = [overall_ci[m]["chrf++"]["point"] for m in methods]
    chrf_errs = [overall_ci[m]["chrf++"]["ci_halfwidth"] for m in methods]
    bar_chart(FIG_ROOT / "chrf.png", "chrF++ (정답 전사 -> 번역, ↑) — 95% CI (n=120)", [labels[m] for m in methods], chrf_vals, chrf_errs, "chrF++")

    rtf_methods = [m for m in methods if "rtf" in overall_ci[m]]
    if rtf_methods:
        rtf_vals = [overall_ci[m]["rtf"]["point"] for m in rtf_methods]
        rtf_errs = [overall_ci[m]["rtf"]["ci_halfwidth"] for m in rtf_methods]
        bar_chart(FIG_ROOT / "rtf.png", "RTF (GPU) — 95% CI, 낮을수록 빠름", [labels[m] for m in rtf_methods], rtf_vals, rtf_errs, "RTF")

    print("### 전체 (95% CI, n=120)\n")
    print("| 방법 | chrF++ (95% CI) | RTF (95% CI) |")
    print("|---|---|---|")
    for m in methods:
        c = overall_ci[m]
        rtf_str = f"{c['rtf']['point']:.3f} ± {c['rtf']['ci_halfwidth']:.3f}" if "rtf" in c else "N/A"
        print(f"| {labels[m]} | {c['chrf++']['point']:.2f} ± {c['chrf++']['ci_halfwidth']:.2f} | {rtf_str} |")

    print("\n### normal/hard 그룹별 chrF++ (95% CI)\n")
    print("| 방법 | normal | hard |")
    print("|---|---|---|")
    for m in methods:
        cells = []
        for g in ["normal", "hard"]:
            if g in group_ci[m]:
                c = group_ci[m][g]["chrf++"]
                cells.append(f"{c['point']:.2f} ± {c['ci_halfwidth']:.2f}")
            else:
                cells.append("N/A")
        print(f"| {labels[m]} | " + " | ".join(cells) + " |")

    print(f"\nsummary -> {summary_path}\nfigs -> {FIG_ROOT}")


if __name__ == "__main__":
    main()
