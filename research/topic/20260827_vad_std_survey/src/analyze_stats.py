"""Paired bootstrap 95% CI + Holm correction for A vs B VAD-STT gap analysis.

Input: quant_per_segment.jsonl + quant_summary.json from each of 4 runs
  (A_qwen3-asr-1.7b, B_qwen3-asr-1.7b, A_turbo, B_turbo)
  + optional embedding_per_segment.jsonl (cos_sim)

Output:
  - out/stats_summary.json — nested {engine: {metric: {...}}, "rtf": {...}}
  - Markdown table to stdout
  - fig/gap_quality.png, fig/rtf.png, fig/cer_by_category.png

Metrics: CER, chrF++, BLEU-char, ROUGE-L F1, cos_sim (optional).

Bootstrap: 2000 resamples, paired (same indices for A and B), numpy.random.default_rng(20260827).
  - Corpus metrics (CER, chrF++, BLEU): recomputed from resampled segments
  - Mean metrics (ROUGE-L, cos_sim): mean of resampled per-segment values
  - Report: point estimate, 95% percentile CI, bootstrap p (two-sided)

Holm correction: within each engine, 5 or 4 tests (metrics). Sort p-values ascending,
  apply Holm step-down: p_adj[k] = min(1, max(i<=k) of (m - i) * p_sorted[i]).

Verdict: "reached" iff (diff CI contains 0) AND (Holm p > 0.05); else "gap".

Usage:
  python analyze_stats.py
  python analyze_stats.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import jiwer
import numpy as np
import sacrebleu
from matplotlib import pyplot as plt

# Korean category labels appear in figures. Try common CJK fonts across
# Windows (dev) and Linux (GPU box); DejaVu Sans is the last-resort fallback
# (Korean will box, but the run won't fail).
plt.rcParams["font.family"] = [
    "Malgun Gothic", "NanumGothic", "Noto Sans CJK KR", "AppleGothic", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CATEGORIES, normalize_ja

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
FIG_ROOT = TOPIC_ROOT / "fig"

N_BOOT = 2000
SEED = 20260827


def load_per_segment(run: str) -> list[dict]:
    """Load quant_per_segment.jsonl for a run. Return list of dicts with seg_id, ref_norm, hyp_norm, metrics, stt_elapsed_s."""
    path = OUT_ROOT / run / "quant_per_segment.jsonl"
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def load_embedding(run: str) -> dict[str, float] | None:
    """Load embedding_per_segment.jsonl if it exists. Return dict {seg_id: cos_sim}."""
    path = OUT_ROOT / run / "embedding_per_segment.jsonl"
    if not path.exists():
        return None
    result = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            result[row["seg_id"]] = row["cos_sim"]
    return result


def load_summary(run: str) -> dict:
    """Load quant_summary.json."""
    path = OUT_ROOT / run / "quant_summary.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def join_runs(
    run_a: str, run_b: str
) -> tuple[list[dict], list[dict], dict[str, float] | None, dict[str, float] | None]:
    """Load A and B, join on seg_id (inner join). Return
    (rows_a, rows_b, embeddings_a or None, embeddings_b or None) — each run's
    OWN cos_sim (cosine of that run's hyp vs the JA label), never shared.
    Rows are ordered by seg_id to ensure matched pairing.
    """
    rows_a = load_per_segment(run_a)
    rows_b = load_per_segment(run_b)
    embed_a = load_embedding(run_a)
    embed_b = load_embedding(run_b)

    # Index by seg_id
    idx_a = {r["seg_id"]: r for r in rows_a}
    idx_b = {r["seg_id"]: r for r in rows_b}

    # Inner join on seg_id
    shared_ids = sorted(set(idx_a.keys()) & set(idx_b.keys()))
    if len(shared_ids) != len(idx_a) or len(shared_ids) != len(idx_b):
        print(f"WARNING: run mismatch. |A|={len(idx_a)}, |B|={len(idx_b)}, |A∩B|={len(shared_ids)}", file=sys.stderr)

    rows_a_joined = [idx_a[seg_id] for seg_id in shared_ids]
    rows_b_joined = [idx_b[seg_id] for seg_id in shared_ids]
    embed_a_joined = {sid: embed_a[sid] for sid in shared_ids} if embed_a else None
    embed_b_joined = {sid: embed_b[sid] for sid in shared_ids} if embed_b else None

    return rows_a_joined, rows_b_joined, embed_a_joined, embed_b_joined


def corpus_cer(refs_norm: list[str], hyps_norm: list[str]) -> float:
    """Recompute corpus CER from normalized strings."""
    return jiwer.cer(refs_norm, hyps_norm) if refs_norm else 0.0


def corpus_chrf(refs_norm: list[str], hyps_norm: list[str]) -> float:
    """Recompute corpus chrF++."""
    return sacrebleu.corpus_chrf(hyps_norm, [refs_norm], word_order=2).score if refs_norm else 0.0


def corpus_bleu(refs_norm: list[str], hyps_norm: list[str]) -> float:
    """Recompute corpus BLEU-char."""
    return sacrebleu.corpus_bleu(hyps_norm, [refs_norm], tokenize="char").score if refs_norm else 0.0


def bootstrap_paired(
    rows_a: list[dict],
    rows_b: list[dict],
    embeddings_a: dict[str, float] | None,
    embeddings_b: dict[str, float] | None,
    include_cos_sim: bool,
) -> dict[str, dict]:
    """Perform paired bootstrap on A vs B. Return dict {metric: result_dict}."""
    rng = np.random.default_rng(SEED)
    n = len(rows_a)

    # ref_norm / hyp_norm are already normalized by score_quantitative.py;
    # normalize_ja is idempotent so re-applying is a harmless safety net.
    refs_a = [normalize_ja(r["ref_norm"]) for r in rows_a]
    hyps_a = [normalize_ja(r["hyp_norm"]) for r in rows_a]
    hyps_b = [normalize_ja(r["hyp_norm"]) for r in rows_b]

    # Corpus metrics: point
    pt_cer_a = corpus_cer(refs_a, hyps_a)
    pt_chrf_a = corpus_chrf(refs_a, hyps_a)
    pt_bleu_a = corpus_bleu(refs_a, hyps_a)
    pt_cer_b = corpus_cer(refs_a, hyps_b)
    pt_chrf_b = corpus_chrf(refs_a, hyps_b)
    pt_bleu_b = corpus_bleu(refs_a, hyps_b)

    # Mean metrics: point
    pt_rouge_a = np.mean([r["rouge_l_f1"] for r in rows_a])
    pt_rouge_b = np.mean([r["rouge_l_f1"] for r in rows_b])
    pt_cos_sim_a = np.mean([embeddings_a[r["seg_id"]] for r in rows_a]) if include_cos_sim else None
    pt_cos_sim_b = np.mean([embeddings_b[r["seg_id"]] for r in rows_b]) if include_cos_sim else None

    # Bootstrap resampling
    samples_cer_a = []
    samples_chrf_a = []
    samples_bleu_a = []
    samples_rouge_a = []
    samples_cos_sim_a = [] if include_cos_sim else None
    samples_cer_b = []
    samples_chrf_b = []
    samples_bleu_b = []
    samples_rouge_b = []
    samples_cos_sim_b = [] if include_cos_sim else None

    for _ in range(N_BOOT):
        # Draw same indices for both A and B (paired)
        indices = rng.integers(0, n, n)

        # A: corpus metrics recomputed
        refs_resample = [refs_a[i] for i in indices]
        hyps_resample_a = [hyps_a[i] for i in indices]
        hyps_resample_b = [hyps_b[i] for i in indices]

        samples_cer_a.append(corpus_cer(refs_resample, hyps_resample_a))
        samples_chrf_a.append(corpus_chrf(refs_resample, hyps_resample_a))
        samples_bleu_a.append(corpus_bleu(refs_resample, hyps_resample_a))

        # B: corpus metrics recomputed
        samples_cer_b.append(corpus_cer(refs_resample, hyps_resample_b))
        samples_chrf_b.append(corpus_chrf(refs_resample, hyps_resample_b))
        samples_bleu_b.append(corpus_bleu(refs_resample, hyps_resample_b))

        # Mean metrics (per-segment)
        samples_rouge_a.append(np.mean([rows_a[i]["rouge_l_f1"] for i in indices]))
        samples_rouge_b.append(np.mean([rows_b[i]["rouge_l_f1"] for i in indices]))

        if include_cos_sim:
            samples_cos_sim_a.append(np.mean([embeddings_a[rows_a[i]["seg_id"]] for i in indices]))
            samples_cos_sim_b.append(np.mean([embeddings_b[rows_b[i]["seg_id"]] for i in indices]))

    # Compute CIs and p-values
    def compute_ci_and_p(point_a: float, point_b: float, samples_a: list, samples_b: list) -> dict:
        """Return {point_A, point_B, point_diff, ci_A, ci_B, ci_diff, p_raw}."""
        samples_a = np.array(samples_a)
        samples_b = np.array(samples_b)
        diff = samples_a - samples_b

        # Percentile CI
        ci_lo_a = np.percentile(samples_a, 2.5)
        ci_hi_a = np.percentile(samples_a, 97.5)
        ci_lo_b = np.percentile(samples_b, 2.5)
        ci_hi_b = np.percentile(samples_b, 97.5)
        ci_lo_diff = np.percentile(diff, 2.5)
        ci_hi_diff = np.percentile(diff, 97.5)

        # Two-sided p: how many resamples are as or more extreme than 0?
        p_raw = 2 * min(np.mean(diff <= 0), np.mean(diff >= 0))
        p_raw = min(1.0, max(0.0, p_raw))

        return {
            "point_A": float(point_a),
            "point_B": float(point_b),
            "point_diff": float(point_a - point_b),
            "ci_A": [float(ci_lo_a), float(ci_hi_a)],
            "ci_B": [float(ci_lo_b), float(ci_hi_b)],
            "ci_diff": [float(ci_lo_diff), float(ci_hi_diff)],
            "p_raw": float(p_raw),
        }

    result = {
        "cer": compute_ci_and_p(pt_cer_a, pt_cer_b, samples_cer_a, samples_cer_b),
        "chrf++": compute_ci_and_p(pt_chrf_a, pt_chrf_b, samples_chrf_a, samples_chrf_b),
        "bleu_char": compute_ci_and_p(pt_bleu_a, pt_bleu_b, samples_bleu_a, samples_bleu_b),
        "rouge_l_f1": compute_ci_and_p(pt_rouge_a, pt_rouge_b, samples_rouge_a, samples_rouge_b),
    }

    if include_cos_sim:
        result["cos_sim"] = compute_ci_and_p(pt_cos_sim_a, pt_cos_sim_b, samples_cos_sim_a, samples_cos_sim_b)

    return result


def holm_correction(p_values: list[float]) -> list[float]:
    """Apply Holm step-down correction. Return p_adj in original order.

    p_values: list of raw p-values (arbitrary order)
    Returns: list of adjusted p-values (same order as input)

    Algorithm: sort by p, apply step-down, un-sort to original order.
    """
    m = len(p_values)
    # (index, p_value) sorted by p
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    p_adj_sorted = []
    max_so_far = 0.0
    for k, (orig_idx, p) in enumerate(indexed):
        adj = min(1.0, max(max_so_far, (m - k) * p))
        p_adj_sorted.append(adj)
        max_so_far = adj

    # Un-sort
    p_adj = [0.0] * m
    for k, (orig_idx, _) in enumerate(indexed):
        p_adj[orig_idx] = p_adj_sorted[k]

    return p_adj


def apply_holm_and_verdict(stats: dict[str, dict], metric_names: list[str]) -> None:
    """Mutate stats in-place: add p_holm and verdict to each metric.

    metrics: [metric_names in some order, e.g., ["cer", "chrf++", "bleu_char", "rouge_l_f1", "cos_sim"]]
    """
    # Gather raw p-values in order
    p_values = [stats[m]["p_raw"] for m in metric_names if m in stats]

    # Apply Holm
    p_holm_list = holm_correction(p_values)

    # Assign back
    for i, m in enumerate(metric_names):
        if m in stats:
            stats[m]["p_holm"] = float(p_holm_list[i])

            # Determine verdict: "reached" iff CI contains 0 AND p_holm > 0.05
            ci_diff_lo, ci_diff_hi = stats[m]["ci_diff"]
            contains_zero = ci_diff_lo <= 0 <= ci_diff_hi
            p_passed = p_holm_list[i] > 0.05

            # Determine direction: which is better?
            # Lower is better for cer; higher is better for chrf++, bleu_char, rouge_l_f1, cos_sim
            better_direction = ""
            if m == "cer":
                if stats[m]["point_diff"] < 0:
                    better_direction = "A (lower CER)"
                elif stats[m]["point_diff"] > 0:
                    better_direction = "B (lower CER)"
            else:  # higher is better
                if stats[m]["point_diff"] > 0:
                    better_direction = "A (higher)"
                elif stats[m]["point_diff"] < 0:
                    better_direction = "B (higher)"

            if contains_zero and p_passed:
                verdict = "reached"
            else:
                verdict = "gap"
                if better_direction:
                    verdict += f" ({better_direction})"

            stats[m]["verdict"] = verdict


def rtf_bootstrap(run_a: str, run_b: str) -> dict:
    """Compute point RTF from summaries and bootstrap CI on the ratio RTF(A)/RTF(B)."""
    summary_a = load_summary(run_a)
    summary_b = load_summary(run_b)

    rtf_a = summary_a.get("rtf", 0.0)
    rtf_b = summary_b.get("rtf", 0.0)

    # Load per-segment data for bootstrap
    rows_a, rows_b, _, _ = join_runs(run_a, run_b)

    # Bootstrap the ratio
    rng = np.random.default_rng(SEED)
    n = len(rows_a)
    ratios = []
    for _ in range(N_BOOT):
        indices = rng.integers(0, n, n)
        stt_a = sum(rows_a[i].get("stt_elapsed_s") or 0.0 for i in indices)
        stt_b = sum(rows_b[i].get("stt_elapsed_s") or 0.0 for i in indices)
        dur = sum(rows_a[i]["duration_s"] for i in indices)

        if dur > 0 and stt_b > 0:
            rtf_a_resamp = stt_a / dur
            rtf_b_resamp = stt_b / dur
            ratios.append(rtf_a_resamp / rtf_b_resamp if rtf_b_resamp > 0 else 0.0)

    if ratios:
        ci_lo = np.percentile(ratios, 2.5)
        ci_hi = np.percentile(ratios, 97.5)
        ratio = rtf_a / rtf_b if rtf_b > 0 else 0.0
    else:
        ci_lo = ci_hi = ratio = 0.0

    return {
        "rtf_A": float(rtf_a),
        "rtf_B": float(rtf_b),
        "ratio_A_B": float(ratio),
        "ratio_ci": [float(ci_lo), float(ci_hi)],
    }


def cer_by_category(run_a: str, run_b: str, engine: str) -> dict[str, dict]:
    """Compute per-category CER with bootstrap CI. Return {category: {point_A, point_B, ci_A, ci_B, ...}}."""
    rows_a, rows_b, _, _ = join_runs(run_a, run_b)

    result = {}
    rng = np.random.default_rng(SEED)
    n = len(rows_a)

    for cat in CATEGORIES:
        indices_cat = [i for i, r in enumerate(rows_a) if r["category"] == cat]
        if not indices_cat:
            continue

        # Point estimate
        refs_norm = [rows_a[i]["ref_norm"] for i in indices_cat]
        hyps_a_norm = [rows_a[i]["hyp_norm"] for i in indices_cat]
        hyps_b_norm = [rows_b[i]["hyp_norm"] for i in indices_cat]

        pt_cer_a = corpus_cer(refs_norm, hyps_a_norm)
        pt_cer_b = corpus_cer(refs_norm, hyps_b_norm)

        # Bootstrap
        samples_a = []
        samples_b = []
        for _ in range(N_BOOT):
            indices_resample = rng.choice(indices_cat, len(indices_cat), replace=True)
            refs_resamp = [rows_a[i]["ref_norm"] for i in indices_resample]
            hyps_a_resamp = [rows_a[i]["hyp_norm"] for i in indices_resample]
            hyps_b_resamp = [rows_b[i]["hyp_norm"] for i in indices_resample]

            samples_a.append(corpus_cer(refs_resamp, hyps_a_resamp))
            samples_b.append(corpus_cer(refs_resamp, hyps_b_resamp))

        samples_a = np.array(samples_a)
        samples_b = np.array(samples_b)

        ci_lo_a = np.percentile(samples_a, 2.5)
        ci_hi_a = np.percentile(samples_a, 97.5)
        ci_lo_b = np.percentile(samples_b, 2.5)
        ci_hi_b = np.percentile(samples_b, 97.5)

        result[cat] = {
            "point_A": float(pt_cer_a),
            "point_B": float(pt_cer_b),
            "ci_A": [float(ci_lo_a), float(ci_hi_a)],
            "ci_B": [float(ci_lo_b), float(ci_hi_b)],
        }

    return result


def plot_gap_quality(stats_by_engine: dict[str, dict]) -> None:
    """Plot quality metrics (A vs B with CIs) per engine. One fig with 2 subplots (one per engine)."""
    FIG_ROOT.mkdir(parents=True, exist_ok=True)

    metrics_to_plot = ["cer", "chrf++", "bleu_char", "rouge_l_f1", "cos_sim"]
    better_dir = {
        "cer": "↓ better",
        "chrf++": "↑ better",
        "bleu_char": "↑ better",
        "rouge_l_f1": "↑ better",
        "cos_sim": "↑ better",
    }

    # One row per engine, one subplot per metric? Or multi-row? Keep it simple: stack engines vertically.
    n_metrics = 0
    for engine in stats_by_engine:
        n_metrics = len([m for m in metrics_to_plot if m in stats_by_engine[engine]])
        break

    fig, axes = plt.subplots(
        len(stats_by_engine), n_metrics, figsize=(4 * n_metrics, 3.5 * len(stats_by_engine))
    )
    if len(stats_by_engine) == 1:
        axes = [axes]
    if n_metrics == 1:
        axes = [[ax] for ax in axes]

    for row_idx, engine in enumerate(sorted(stats_by_engine.keys())):
        stats = stats_by_engine[engine]
        for col_idx, metric in enumerate(metrics_to_plot):
            if metric not in stats:
                continue
            ax = axes[row_idx][col_idx]

            m = stats[metric]
            pt_a = m["point_A"]
            pt_b = m["point_B"]
            ci_a = m["ci_A"]
            ci_b = m["ci_B"]
            err_a = (pt_a - ci_a[0], ci_a[1] - pt_a)
            err_b = (pt_b - ci_b[0], ci_b[1] - pt_b)

            x = [0, 1]
            y = [pt_a, pt_b]
            yerr = [err_a, err_b]

            ax.bar(x, y, yerr=yerr, capsize=4, alpha=0.7, color=["C0", "C1"], width=0.6)
            ax.set_xticks(x)
            ax.set_xticklabels(["A (pipeline)", "B (baseline)"])
            ax.set_ylabel(metric)
            ax.set_title(f"{metric} {better_dir.get(metric, '')}")
            ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Quality Gap (A vs B)", fontsize=14, fontweight="bold")
    fig.tight_layout()
    fig.savefig(FIG_ROOT / "gap_quality.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_rtf(rtf_by_run: dict[str, float]) -> None:
    """Plot RTF for the 4 runs with log scale."""
    FIG_ROOT.mkdir(parents=True, exist_ok=True)

    runs = sorted(rtf_by_run.keys())
    values = [rtf_by_run[r] for r in runs]

    fig, ax = plt.subplots(figsize=(6, 4))
    bars = ax.bar(range(len(runs)), values, color=["C0", "C1", "C0", "C1"], alpha=0.7, width=0.6)
    ax.set_yscale("log")
    ax.set_xticks(range(len(runs)))
    ax.set_xticklabels(runs, rotation=45, ha="right")
    ax.set_ylabel("RTF (log scale)")
    ax.set_title("Speed: Real-Time Factor")
    ax.grid(axis="y", alpha=0.3)

    # Add value labels on bars
    for i, (bar, val) in enumerate(zip(bars, values)):
        ax.text(bar.get_x() + bar.get_width() / 2, val * 1.15, f"{val:.3f}", ha="center", fontsize=9)

    fig.tight_layout()
    fig.savefig(FIG_ROOT / "rtf.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def plot_cer_by_category(cer_by_cat_per_engine: dict[str, dict[str, dict]]) -> None:
    """Plot per-category CER with bootstrap CIs. One subplot per engine."""
    FIG_ROOT.mkdir(parents=True, exist_ok=True)

    engines = sorted(cer_by_cat_per_engine.keys())
    fig, axes = plt.subplots(1, len(engines), figsize=(5 * len(engines), 4))
    if len(engines) == 1:
        axes = [axes]

    for ax, engine in zip(axes, engines):
        cer_by_cat = cer_by_cat_per_engine[engine]
        cats = sorted(cer_by_cat.keys())

        x_pos = np.arange(len(cats))
        width = 0.35

        pts_a = [cer_by_cat[c]["point_A"] for c in cats]
        pts_b = [cer_by_cat[c]["point_B"] for c in cats]
        errs_a = [
            (cer_by_cat[c]["point_A"] - cer_by_cat[c]["ci_A"][0], cer_by_cat[c]["ci_A"][1] - cer_by_cat[c]["point_A"])
            for c in cats
        ]
        errs_b = [
            (cer_by_cat[c]["point_B"] - cer_by_cat[c]["ci_B"][0], cer_by_cat[c]["ci_B"][1] - cer_by_cat[c]["point_B"])
            for c in cats
        ]
        errs_a = list(zip(*errs_a))
        errs_b = list(zip(*errs_b))

        ax.bar(x_pos - width / 2, pts_a, width, label="A (pipeline)", yerr=errs_a, capsize=3, alpha=0.7, color="C0")
        ax.bar(x_pos + width / 2, pts_b, width, label="B (baseline)", yerr=errs_b, capsize=3, alpha=0.7, color="C1")

        ax.set_xticks(x_pos)
        ax.set_xticklabels(cats, rotation=45, ha="right", fontsize=9)
        ax.set_ylabel("CER (↓ better)")
        ax.set_title(f"CER by Category — {engine}")
        ax.legend()
        ax.grid(axis="y", alpha=0.3)

    fig.tight_layout()
    fig.savefig(FIG_ROOT / "cer_by_category.png", dpi=150, bbox_inches="tight")
    plt.close(fig)


def run_check() -> None:
    """Synthetic fixture check: create fake data, run analysis, verify outputs."""
    global OUT_ROOT, FIG_ROOT

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        out_path = tmp_path / "out"
        out_path.mkdir()

        # Create fake runs
        runs = ["A_qwen3-asr-1.7b", "B_qwen3-asr-1.7b", "A_turbo", "B_turbo"]
        for run in runs:
            run_dir = out_path / run
            run_dir.mkdir()

            # 20 fake segments
            rows = []
            for seg_id in range(20):
                rows.append({
                    "seg_id": f"seg_{seg_id:02d}",
                    "category": CATEGORIES[seg_id % len(CATEGORIES)],
                    "duration_s": 2.0,
                    "ref_norm": "テストテスト",
                    "hyp_norm": "テストテスト" if seg_id % 2 == 0 else "テスト",
                    "cer": 0.0 if seg_id % 2 == 0 else 0.5,
                    "chrf++": 100.0 if seg_id % 2 == 0 else 50.0,
                    "bleu_char": 100.0 if seg_id % 2 == 0 else 50.0,
                    "rouge_l_f1": 1.0 if seg_id % 2 == 0 else 0.5,
                    "stt_elapsed_s": 0.5,
                })

            # Write JSONL
            per_seg_path = run_dir / "quant_per_segment.jsonl"
            with per_seg_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

            # Write summary JSON
            summary_path = run_dir / "quant_summary.json"
            with summary_path.open("w", encoding="utf-8") as f:
                json.dump({"rtf": 0.1, "total_stt_s": 10.0, "total_audio_s": 100.0}, f)

            # Write embedding JSONL (optional)
            embed_path = run_dir / "embedding_per_segment.jsonl"
            with embed_path.open("w", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps({"seg_id": row["seg_id"], "cos_sim": 0.9}, ensure_ascii=False) + "\n")

        # Save and patch globals
        orig_out_root = OUT_ROOT
        orig_fig_root = FIG_ROOT
        OUT_ROOT = out_path
        fig_path = tmp_path / "fig"
        FIG_ROOT = fig_path

        try:
            # Run the main flow
            stats_by_engine = {}
            rtf_data = {}
            cer_by_cat_per_engine = {}

            for engine, run_a, run_b in [
                ("qwen3-asr-1.7b", "A_qwen3-asr-1.7b", "B_qwen3-asr-1.7b"),
                ("turbo", "A_turbo", "B_turbo"),
            ]:
                rows_a, rows_b, embed_a, embed_b = join_runs(run_a, run_b)
                has_embed = embed_a is not None and embed_b is not None
                stats = bootstrap_paired(rows_a, rows_b, embed_a, embed_b, has_embed)

                # Apply Holm
                metric_names = [m for m in ["cer", "chrf++", "bleu_char", "rouge_l_f1", "cos_sim"] if m in stats]
                apply_holm_and_verdict(stats, metric_names)

                stats_by_engine[engine] = stats

                # RTF
                rtf_data[engine] = rtf_bootstrap(run_a, run_b)

                # CER by category
                cer_by_cat_per_engine[engine] = cer_by_category(run_a, run_b, engine)

            # Generate figures
            plot_gap_quality(stats_by_engine)
            plot_rtf({run: load_summary(run)["rtf"] for run in runs})
            plot_cer_by_category(cer_by_cat_per_engine)

            # Validate
            for engine in stats_by_engine:
                for metric in stats_by_engine[engine]:
                    m = stats_by_engine[engine][metric]
                    pt = m["point_diff"]
                    ci_lo, ci_hi = m["ci_diff"]
                    assert ci_lo <= pt <= ci_hi, f"{engine} {metric}: CI doesn't contain point"
                    assert 0 <= m["p_raw"] <= 1, f"{engine} {metric}: p_raw out of [0,1]"
                    assert 0 <= m["p_holm"] <= 1, f"{engine} {metric}: p_holm out of [0,1]"
                    assert m["p_holm"] >= m["p_raw"], f"{engine} {metric}: Holm p not >= raw p"
                    assert m["verdict"] in ["reached", "gap", "gap (A (lower CER))", "gap (B (lower CER))"], f"{engine} {metric}: invalid verdict"

            # Check figures exist
            assert (fig_path / "gap_quality.png").exists(), "gap_quality.png not written"
            assert (fig_path / "rtf.png").exists(), "rtf.png not written"
            assert (fig_path / "cer_by_category.png").exists(), "cer_by_category.png not written"

            for fig_name in ["gap_quality.png", "rtf.png", "cer_by_category.png"]:
                fig_file = fig_path / fig_name
                assert fig_file.stat().st_size > 0, f"{fig_name} is empty"

            print("ok")

        finally:
            OUT_ROOT = orig_out_root
            FIG_ROOT = orig_fig_root


def main() -> None:
    parser = argparse.ArgumentParser(description="Paired bootstrap stats for A vs B VAD-STT gap analysis.")
    parser.add_argument("--check", action="store_true", help="Run validation check and exit")
    args = parser.parse_args()

    if args.check:
        run_check()
        return

    # Load and process each engine
    stats_by_engine = {}
    rtf_data = {}
    cer_by_cat_per_engine = {}

    for engine, run_a, run_b in [
        ("qwen3-asr-1.7b", "A_qwen3-asr-1.7b", "B_qwen3-asr-1.7b"),
        ("turbo", "A_turbo", "B_turbo"),
    ]:
        rows_a, rows_b, embed_a, embed_b = join_runs(run_a, run_b)
        has_embed = embed_a is not None and embed_b is not None
        stats = bootstrap_paired(rows_a, rows_b, embed_a, embed_b, has_embed)

        # Apply Holm correction
        metric_names = [m for m in ["cer", "chrf++", "bleu_char", "rouge_l_f1", "cos_sim"] if m in stats]
        apply_holm_and_verdict(stats, metric_names)

        stats_by_engine[engine] = stats

        # RTF analysis
        rtf_data[engine] = rtf_bootstrap(run_a, run_b)

        # CER by category
        cer_by_cat_per_engine[engine] = cer_by_category(run_a, run_b, engine)

    # Generate figures
    plot_gap_quality(stats_by_engine)
    all_rtf = {run: load_summary(run)["rtf"] for run in ["A_qwen3-asr-1.7b", "B_qwen3-asr-1.7b", "A_turbo", "B_turbo"]}
    plot_rtf(all_rtf)
    plot_cer_by_category(cer_by_cat_per_engine)

    # Prepare JSON output
    output = {
        "stats": stats_by_engine,
        "rtf": {
            engine: rtf_data[engine] for engine in sorted(rtf_data.keys())
        },
    }

    # Write JSON
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    json_path = OUT_ROOT / "stats_summary.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Print markdown tables to stdout
    print("# VAD-STT Gap Analysis: Paired Bootstrap Results\n")

    for engine in sorted(stats_by_engine.keys()):
        stats = stats_by_engine[engine]
        print(f"\n## {engine}\n")
        print("| Metric | A [95% CI] | B [95% CI] | A−B [95% CI] | p (raw) | p (Holm) | Verdict |")
        print("|--------|-----------|-----------|--------------|---------|---------|---------|")
        for metric in ["cer", "chrf++", "bleu_char", "rouge_l_f1", "cos_sim"]:
            if metric not in stats:
                continue
            m = stats[metric]
            a_ci = f"{m['point_A']:.3f} [{m['ci_A'][0]:.3f}, {m['ci_A'][1]:.3f}]"
            b_ci = f"{m['point_B']:.3f} [{m['ci_B'][0]:.3f}, {m['ci_B'][1]:.3f}]"
            diff_ci = f"{m['point_diff']:.3f} [{m['ci_diff'][0]:.3f}, {m['ci_diff'][1]:.3f}]"
            print(
                f"| {metric} | {a_ci} | {b_ci} | {diff_ci} | {m['p_raw']:.4f} | {m['p_holm']:.4f} | {m['verdict']} |"
            )

    print("\n## RTF (Speed)\n")
    print("| Engine | RTF A | RTF B | Ratio A/B [95% CI] |")
    print("|--------|-------|-------|-------------------|")
    for engine in sorted(rtf_data.keys()):
        r = rtf_data[engine]
        ratio_ci = f"{r['ratio_A_B']:.3f} [{r['ratio_ci'][0]:.3f}, {r['ratio_ci'][1]:.3f}]"
        print(f"| {engine} | {r['rtf_A']:.4f} | {r['rtf_B']:.4f} | {ratio_ci} |")

    # Plain-language summary
    print("\n## Summary\n")
    for engine in sorted(stats_by_engine.keys()):
        stats = stats_by_engine[engine]
        gap_count = sum(1 for m in stats.values() if "gap" in m["verdict"])
        reached_count = sum(1 for m in stats.values() if m["verdict"] == "reached")
        total = len(stats)
        print(
            f"**{engine}**: {reached_count}/{total} metrics reached. {gap_count}/{total} show a gap. "
            + ("A reached parity with B." if gap_count == 0 else f"A lags B on {gap_count} metric(s).")
        )

    print(f"\n✓ Wrote {json_path}")
    print(f"✓ Wrote figures to {FIG_ROOT}/")


if __name__ == "__main__":
    main()
