"""Manual qualitative eval for the VAD-STT gap survey — a human (Claude,
2026-08-27) scores the full 150-segment census on three axes (naturalness,
punctuation, hallucination flag), then this script aggregates with Wilcoxon
+ Holm correction + paired bootstrap.

Four runs (2 engines × 2 paths: A=VAD pipeline, B=direct):
  - A_qwen3-asr-1.7b: vad_std_survey/out/qwen3-asr-1.7b/pipeline_transcripts.jsonl
  - B_qwen3-asr-1.7b: stt_model_survey_gpu_full/out/qwen3-asr-1.7b/transcripts.jsonl
  - A_turbo: vad_std_survey/out/turbo/pipeline_transcripts.jsonl
  - B_turbo: stt_model_survey_gpu_full/out/turbo/transcripts.jsonl

Three axes, each 1 (전혀 그렇지 않음) .. 5 (아주 그러함):
  - naturalness: 자연스러움·가독성 (표기, 읽기 흐름)
  - punctuation: 문장부호 엄수성 (?, !, . 등)
  - hallucination: 환각 0/1 플래그 (원문에 없는 내용)

Stats per DESIGN.md §8.3:
  - Wilcoxon signed-rank on naturalness + punctuation (A vs B, per engine)
  - Holm correction (α=0.05, m=4: 2 axes × 2 engines)
  - Paired bootstrap 2000 on hallucination-rate difference

Usage:
  python qualitative_eval.py sample   # (re)write out/qualitative_sample.txt
  python qualitative_eval.py agg      # aggregate out/qualitative_scores.json
  python qualitative_eval.py --check  # self-check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
SIBLING_TOPIC = TOPIC_ROOT.parent / "20260826_stt_model_survey_gpu_full" / "out"

# Import CATEGORIES from common.py (in same directory)
sys.path.insert(0, str(TOPIC_ROOT / "src"))
from common import CATEGORIES

RUNS = ["A_qwen3-asr-1.7b", "B_qwen3-asr-1.7b", "A_turbo", "B_turbo"]
SAMPLE_TXT = OUT_ROOT / "qualitative_sample.txt"
SCORES_JSON = OUT_ROOT / "qualitative_scores.json"
AXES = ["naturalness", "punctuation"]
RATE_AXIS = "hallucination"


def _load_transcripts(run: str) -> dict[str, dict]:
    """Load transcripts.jsonl for a run (A or B variant)."""
    if run.startswith("A_"):
        engine = run.split("_", 1)[1]
        path = OUT_ROOT / engine / "pipeline_transcripts.jsonl"
    else:  # B_
        engine = run.split("_", 1)[1]
        path = SIBLING_TOPIC / engine / "transcripts.jsonl"

    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    return {r["seg_id"]: r for r in rows}


def _build_ordered_seg_ids() -> list[str]:
    """All 150 seg_ids, ordered by category then seg_id — deterministic."""
    data = _load_transcripts("A_qwen3-asr-1.7b")
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for seg_id, r in data.items():
        by_cat[r["category"]].append(seg_id)

    seg_ids: list[str] = []
    for cat in CATEGORIES:
        seg_ids.extend(sorted(by_cat[cat]))
    return seg_ids


def cmd_sample() -> None:
    """Write qualitative_sample.txt: all 150 blocks, ordered, 4-run align."""
    seg_ids = _build_ordered_seg_ids()
    data = {run: _load_transcripts(run) for run in RUNS}

    lines: list[str] = []
    for i, seg_id in enumerate(seg_ids, 1):
        base = data["A_qwen3-asr-1.7b"][seg_id]
        lines.append(f"### [{i:03d}] {seg_id}  ({base['category']})")
        lines.append(f"JA_REF : {base['ja_ref']}")
        lines.append(f"KO_REF : {base['ko_ref']}")
        for run in RUNS:
            # Both A (pipeline_transcripts) and B (sibling transcripts) store
            # the transcript under 'hyp'.
            hyp = data[run][seg_id].get("hyp", "")
            lines.append(f"  {run:<18}: {hyp}")
        lines.append("")

    SAMPLE_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(seg_ids)} blocks -> {SAMPLE_TXT}")


def cmd_agg() -> None:
    """Load qualitative_scores.json, validate, aggregate with Wilcoxon + Holm + bootstrap."""
    scores_raw = json.loads(SCORES_JSON.read_text(encoding="utf-8"))
    seg_ids = _build_ordered_seg_ids()

    # Validate: exactly 4 runs, 150 seg_ids each, correct axis ranges
    assert set(scores_raw) == set(RUNS), (
        f"runs mismatch: expected {set(RUNS)}, got {set(scores_raw)}"
    )
    for run in RUNS:
        assert set(scores_raw[run]) == set(seg_ids), (
            f"{run}: seg_ids mismatch: missing {set(seg_ids) - set(scores_raw[run])}, "
            f"extra {set(scores_raw[run]) - set(seg_ids)}"
        )
        for seg_id, scores_per_seg in scores_raw[run].items():
            for ax in AXES:
                val = scores_per_seg[ax]
                assert isinstance(val, int) and 1 <= val <= 5, (
                    f"{run}/{seg_id}/{ax}: {val} not in 1-5"
                )
            assert scores_per_seg[RATE_AXIS] in (0, 1), (
                f"{run}/{seg_id}/{RATE_AXIS}: {scores_per_seg[RATE_AXIS]} not 0/1"
            )

    data = {run: _load_transcripts(run) for run in RUNS}
    cat_of = {sid: data["A_qwen3-asr-1.7b"][sid]["category"] for sid in seg_ids}

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0

    # 1. Per-run means
    print("### 전체 평균 (n=150)\n")
    print("| 실행 | 자연스러움·가독성 | 문장부호 엄수성 | 환각률 (n) |")
    print("|---|---|---|---|")
    for run in RUNS:
        nats = [scores_raw[run][sid]["naturalness"] for sid in seg_ids]
        puncts = [scores_raw[run][sid]["punctuation"] for sid in seg_ids]
        halls = [scores_raw[run][sid][RATE_AXIS] for sid in seg_ids]
        hall_rate = mean(halls)
        hall_count = sum(halls)
        print(f"| {run} | {mean(nats):.2f} | {mean(puncts):.2f} | {hall_rate:.1%} ({int(hall_count)}/150) |")
    print()

    # 2. Per-category tables (naturalness, punctuation, hallucination-rate)
    for ax, title in [("naturalness", "자연스러움·가독성"), ("punctuation", "문장부호 엄수성")]:
        print(f"### 카테고리별 {title}\n")
        print("| 카테고리 | " + " | ".join(RUNS) + " |")
        print("|---|" + "---|" * len(RUNS))
        for cat in CATEGORIES:
            sids = [sid for sid in seg_ids if cat_of[sid] == cat]
            cells = [f"{mean([scores_raw[run][sid][ax] for sid in sids]):.2f}" for run in RUNS]
            print(f"| {cat} (n={len(sids)}) | " + " | ".join(cells) + " |")
        print()

    # Hallucination rate by category
    print("### 카테고리별 환각률\n")
    print("| 카테고리 | " + " | ".join(RUNS) + " |")
    print("|---|" + "---|" * len(RUNS))
    for cat in CATEGORIES:
        sids = [sid for sid in seg_ids if cat_of[sid] == cat]
        cells = [f"{mean([scores_raw[run][sid][RATE_AXIS] for sid in sids]):.0%}" for run in RUNS]
        print(f"| {cat} (n={len(sids)}) | " + " | ".join(cells) + " |")
    print()

    # 3. A vs B: Wilcoxon signed-rank + Holm correction
    print("### A vs B: Wilcoxon signed-rank + Holm correction\n")
    print("| 엔진 | 축 | Wilcoxon W | p (raw) | p (Holm) | A vs B 판정 |")
    print("|---|---|---|---|---|---|")

    # Collect all p-values for Holm correction: (engine, axis, W, p_raw, a_mean, b_mean)
    comparisons = []
    for engine in ["qwen3-asr-1.7b", "turbo"]:
        for ax in AXES:
            a_run = f"A_{engine}"
            b_run = f"B_{engine}"

            a_vals = np.array([scores_raw[a_run][sid][ax] for sid in seg_ids])
            b_vals = np.array([scores_raw[b_run][sid][ax] for sid in seg_ids])

            # Wilcoxon signed-rank, two-sided. Raises if A==B on every segment
            # (all differences zero) — that IS the "identical, so tied" case.
            diff = a_vals - b_vals
            if np.all(diff == 0):
                w_stat, p_raw = 0.0, 1.0
            else:
                w_stat, p_raw = wilcoxon(diff, zero_method="pratt", alternative="two-sided")
            comparisons.append((engine, ax, w_stat, p_raw, a_vals.mean(), b_vals.mean()))

    # Holm correction: p_adj[k] = min(1, max_{j<=k} (m-j)*p_sorted[j])
    ps_raw = [comp[3] for comp in comparisons]
    sorted_idx = sorted(range(len(ps_raw)), key=lambda i: ps_raw[i])
    ps_adj_sorted = []
    m = len(ps_raw)
    for k in range(m):
        max_val = max((m - j) * ps_raw[sorted_idx[j]] for j in range(k + 1))
        ps_adj_sorted.append(min(1.0, max_val))

    # Un-sort
    ps_adj = [0.0] * len(ps_raw)
    for rank, orig_idx in enumerate(sorted_idx):
        ps_adj[orig_idx] = ps_adj_sorted[rank]

    # Print results
    for (engine, ax, w_stat, p_raw, a_mean, b_mean), p_adj in zip(comparisons, ps_adj):
        if p_adj > 0.05:
            verdict = "tied"
        elif a_mean > b_mean:
            verdict = "differ (A better)"
        else:
            verdict = "differ (B better)"

        print(
            f"| {engine} | {ax} | {w_stat:.1f} | {p_raw:.4f} | {p_adj:.4f} | {verdict} |"
        )
    print()

    # 4. Hallucination-rate A−B, paired bootstrap 2000
    print("### 환각률 차이 (A − B): paired bootstrap 95% CI\n")
    print("| 엔진 | 차이 (mean) | 95% CI | bootstrap p |")
    print("|---|---|---|---|")

    rng = np.random.default_rng(20260827)
    for engine in ["qwen3-asr-1.7b", "turbo"]:
        a_run = f"A_{engine}"
        b_run = f"B_{engine}"

        a_rates = np.array([scores_raw[a_run][sid][RATE_AXIS] for sid in seg_ids])
        b_rates = np.array([scores_raw[b_run][sid][RATE_AXIS] for sid in seg_ids])

        diff_obs = a_rates.mean() - b_rates.mean()

        # Bootstrap: resample seg_id indices
        diffs = []
        for _ in range(2000):
            idx = rng.choice(len(seg_ids), size=len(seg_ids), replace=True)
            a_boot = a_rates[idx].mean()
            b_boot = b_rates[idx].mean()
            diffs.append(a_boot - b_boot)

        diffs = np.array(diffs)
        ci_low, ci_high = np.percentile(diffs, [2.5, 97.5])
        p_boot = 2 * min((diffs <= 0).mean(), (diffs >= 0).mean())
        p_boot = min(1.0, p_boot)

        print(
            f"| {engine} | {diff_obs:+.3f} | [{ci_low:+.3f}, {ci_high:+.3f}] | {p_boot:.4f} |"
        )
    print()

    # 5. Score distributions (naturalness + punctuation)
    for ax, title in [
        ("naturalness", "자연스러움·가독성"),
        ("punctuation", "문장부호 엄수성"),
    ]:
        print(f"### {title} 점수 분포 (1 / 2 / 3 / 4 / 5)\n")
        print("| 실행 | 1 | 2 | 3 | 4 | 5 |")
        print("|---|---|---|---|---|---|")
        for run in RUNS:
            dist = [
                sum(1 for sid in seg_ids if scores_raw[run][sid][ax] == k)
                for k in range(1, 6)
            ]
            print(f"| {run} | " + " | ".join(str(d) for d in dist) + " |")
        print()


def _check() -> None:
    """Self-check: sample determinism, value ranges, stats logic."""
    # 1. Sample determinism and size
    try:
        seg_ids = _build_ordered_seg_ids()
    except FileNotFoundError:
        print("ok (transcript files not yet present, skipping file-dependent checks)")
        return

    assert len(seg_ids) == 150, f"expected 150 seg_ids, got {len(seg_ids)}"
    assert len(set(seg_ids)) == 150, "seg_ids has duplicates"
    assert seg_ids == _build_ordered_seg_ids(), "seg_ids not deterministic"

    # Check per-category counts
    data = _load_transcripts("A_qwen3-asr-1.7b")
    by_cat: dict[str, int] = {}
    for sid in seg_ids:
        c = data[sid]["category"]
        by_cat[c] = by_cat.get(c, 0) + 1
    expected_counts = {c: 30 for c in CATEGORIES}
    assert by_cat == expected_counts, f"category counts {by_cat} != {expected_counts}"

    # 2. Test Wilcoxon + Holm on synthetic data
    rng = np.random.default_rng(20260827)
    synthetic_scores = {}
    for run in RUNS:
        synthetic_scores[run] = {}
        for i, sid in enumerate(seg_ids[:6]):  # 6 fake seg_ids
            synthetic_scores[run][sid] = {
                "naturalness": int(rng.integers(1, 6)),
                "punctuation": int(rng.integers(1, 6)),
                "hallucination": int(rng.integers(0, 2)),
            }

    # Wilcoxon should run without error
    for engine in ["qwen3-asr-1.7b", "turbo"]:
        for ax in AXES:
            a_run = f"A_{engine}"
            b_run = f"B_{engine}"
            a_vals = np.array([synthetic_scores[a_run][sid][ax] for sid in seg_ids[:6]])
            b_vals = np.array([synthetic_scores[b_run][sid][ax] for sid in seg_ids[:6]])
            w_stat, p_raw = wilcoxon(
                a_vals - b_vals, zero_method="pratt", alternative="two-sided"
            )
            assert 0 <= p_raw <= 1, f"p_raw {p_raw} out of [0,1]"

    # Holm correction should enforce monotonicity and stay in [0,1]
    ps_raw = [0.001, 0.01, 0.05, 0.1]
    sorted_idx = sorted(range(len(ps_raw)), key=lambda i: ps_raw[i])
    ps_adj_sorted = []
    m = len(ps_raw)
    for k in range(m):
        max_val = max((m - j) * ps_raw[sorted_idx[j]] for j in range(k + 1))
        ps_adj_sorted.append(min(1.0, max_val))

    ps_adj = [0.0] * len(ps_raw)
    for rank, orig_idx in enumerate(sorted_idx):
        ps_adj[orig_idx] = ps_adj_sorted[rank]

    assert all(0 <= p <= 1 for p in ps_adj), f"adjusted p outside [0,1]"
    # Check monotonicity (in original order, not necessarily true; but in sorted order it should be)
    for k in range(1, m):
        assert ps_adj_sorted[k] >= ps_adj_sorted[k - 1], f"Holm not monotonic: {ps_adj_sorted}"

    # 3. Bootstrap on synthetic hallucination rates
    a_rates = np.array(
        [
            synthetic_scores[f"A_qwen3-asr-1.7b"][sid]["hallucination"]
            for sid in seg_ids[:6]
        ]
    )
    b_rates = np.array(
        [
            synthetic_scores[f"B_qwen3-asr-1.7b"][sid]["hallucination"]
            for sid in seg_ids[:6]
        ]
    )
    diffs = []
    for _ in range(100):
        idx = rng.choice(len(a_rates), size=len(a_rates), replace=True)
        diffs.append(a_rates[idx].mean() - b_rates[idx].mean())

    diffs = np.array(diffs)
    p_boot = 2 * min((diffs <= 0).mean(), (diffs >= 0).mean())
    assert 0 <= p_boot <= 1, f"p_boot {p_boot} out of [0,1]"

    print("ok")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif len(sys.argv) > 1 and sys.argv[1] == "sample":
        cmd_sample()
    elif len(sys.argv) > 1 and sys.argv[1] == "agg":
        cmd_agg()
    else:
        print(__doc__)
