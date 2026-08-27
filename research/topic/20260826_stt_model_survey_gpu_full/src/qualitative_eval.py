"""Manual qualitative eval for the GPU full-set ASR survey — a human (Claude,
2026-08-27) reads a stratified 50-segment sample and scores every method's
hypothesis on two 1-5 axes, then this script aggregates.

Two axes (user request 2026-08-27), each 1 (전혀 아님) .. 5 (아주 그러함):
  - naturalness: does the JA hypothesis read as coherent, well-formed Japanese
    fitting that situation/category? (a fluent hallucination still scores high
    here — that's the point: it separates "confidently wrong" from "garbled")
  - fidelity: is the meaning of ja_ref faithfully conveyed? (ko_ref used only
    to disambiguate ja_ref, never scored on its own)

Sample: 10 per category (seed=7), same 50 seg_ids across all 5 methods —
paired, same convention as score_quantitative.py / analyze_ci_and_plot.py.

Usage:
  python qualitative_eval.py sample   # (re)write out/qualitative_sample.txt
  python qualitative_eval.py agg      # aggregate out/qualitative_scores.json
  python qualitative_eval.py --check  # self-check
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"
CATEGORIES = ["게임", "여행", "음식,요리", "일상,소통", "패션,뷰티"]
METHODS = ["turbo", "granite-speech-4.1-2b", "qwen3-asr-0.6b", "qwen3-asr-1.7b", "reazonspeech-nemo-v2"]
LABELS = {
    "turbo": "large-v3-turbo",
    "granite-speech-4.1-2b": "granite-speech-4.1-2b",
    "qwen3-asr-0.6b": "Qwen3-ASR-0.6B",
    "qwen3-asr-1.7b": "Qwen3-ASR-1.7B",
    "reazonspeech-nemo-v2": "ReazonSpeech-NeMo-v2",
}
N_PER_CAT = 10
SEED = 7
SAMPLE_TXT = OUT_ROOT / "qualitative_sample.txt"
SCORES_JSON = OUT_ROOT / "qualitative_scores.json"


def load_method(method: str) -> dict[str, dict]:
    path = OUT_ROOT / method / "transcripts.jsonl"
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    return {r["seg_id"]: r for r in rows}


def pick_sample() -> list[str]:
    base = load_method(METHODS[0])
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for seg_id, r in base.items():
        by_cat[r["category"]].append(seg_id)
    rng = random.Random(SEED)
    picked: list[str] = []
    for cat in CATEGORIES:
        picked.extend(rng.sample(sorted(by_cat[cat]), N_PER_CAT))
    return picked


def cmd_sample() -> None:
    sample = pick_sample()
    data = {m: load_method(m) for m in METHODS}
    lines: list[str] = []
    for i, seg_id in enumerate(sample, 1):
        base = data[METHODS[0]][seg_id]
        lines.append(f"### [{i:02d}] {seg_id}  ({base['category']})  {base['duration_s']:.1f}s")
        lines.append(f"JA_REF : {base['ja_ref']}")
        lines.append(f"KO_REF : {base['ko_ref']}")
        for m in METHODS:
            lines.append(f"  {LABELS[m]:<22}: {data[m][seg_id]['hyp']}")
        lines.append("")
    SAMPLE_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(sample)} segments -> {SAMPLE_TXT}")


def cmd_agg() -> None:
    """qualitative_scores.json shape:
    {"<seg_id>": {"<method>": {"nat": 1-5, "fid": 1-5}, ...}, ...}
    """
    scores = json.loads(SCORES_JSON.read_text(encoding="utf-8"))
    sample = pick_sample()
    data = {m: load_method(m) for m in METHODS}
    cat_of = {sid: data[METHODS[0]][sid]["category"] for sid in sample}

    assert set(scores) == set(sample), (
        f"scored seg_ids != sample: missing {set(sample) - set(scores)}, "
        f"extra {set(scores) - set(sample)}"
    )
    for sid, per_method in scores.items():
        assert set(per_method) == set(METHODS), f"{sid}: methods {set(per_method)}"
        for m, s in per_method.items():
            assert 1 <= s["nat"] <= 5 and 1 <= s["fid"] <= 5, f"{sid}/{m}: out of range {s}"

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    print("### 전체 평균 (n=50)\n")
    print("| 방법 | 자연스러움 (1-5) | 의미 충실도 (1-5) | 유창한 환각률* |")
    print("|---|---|---|---|")
    for m in METHODS:
        nats = [scores[sid][m]["nat"] for sid in sample]
        fids = [scores[sid][m]["fid"] for sid in sample]
        halluc = sum(1 for sid in sample if scores[sid][m]["nat"] >= 4 and scores[sid][m]["fid"] <= 2)
        print(f"| {LABELS[m]} | {mean(nats):.2f} | {mean(fids):.2f} | {halluc}/{len(sample)} ({halluc / len(sample):.0%}) |")
    print("\n\\* 자연스러움 ≥ 4 & 의미 충실도 ≤ 2 = 유창하지만 내용이 틀린 전사\n")

    print("### 카테고리별 의미 충실도 평균 (n=10)\n")
    print("| 카테고리 | " + " | ".join(LABELS[m] for m in METHODS) + " |")
    print("|---|" + "---|" * len(METHODS))
    for cat in CATEGORIES:
        sids = [sid for sid in sample if cat_of[sid] == cat]
        cells = [f"{mean([scores[sid][m]['fid'] for sid in sids]):.2f}" for m in METHODS]
        print(f"| {cat} | " + " | ".join(cells) + " |")

    print("\n### 카테고리별 자연스러움 평균 (n=10)\n")
    print("| 카테고리 | " + " | ".join(LABELS[m] for m in METHODS) + " |")
    print("|---|" + "---|" * len(METHODS))
    for cat in CATEGORIES:
        sids = [sid for sid in sample if cat_of[sid] == cat]
        cells = [f"{mean([scores[sid][m]['nat'] for sid in sids]):.2f}" for m in METHODS]
        print(f"| {cat} | " + " | ".join(cells) + " |")

    print("\n### 의미 충실도 점수 분포 (1 / 2 / 3 / 4 / 5)\n")
    print("| 방법 | 1 | 2 | 3 | 4 | 5 |")
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        dist = [sum(1 for sid in sample if scores[sid][m]["fid"] == k) for k in range(1, 6)]
        print(f"| {LABELS[m]} | " + " | ".join(str(d) for d in dist) + " |")

    # 자연스러움이 유창한 환각에 얼마나 오염됐는지: 충실도 구간별 자연스러움 +
    # 세그먼트 내 r(자연, 충실). 낮은 r = 자연스러움이 충실도의 약한 신호.
    def pearson(xs: list[float], ys: list[float]) -> float:
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys) ** 0.5
        return cov / (sx * sy) if sx and sy else float("nan")

    print("\n### 자연스러움 분해 (유창한 환각 오염 확인)\n")
    print("| 방법 | 자연 전체 | 자연(충실≥3) | 자연(충실≤2) | 유창한 환각률 | r(자연,충실) |")
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        nat = [scores[sid][m]["nat"] for sid in sample]
        fid = [scores[sid][m]["fid"] for sid in sample]
        good = [n for n, f in zip(nat, fid) if f >= 3]
        bad = [n for n, f in zip(nat, fid) if f <= 2]
        halluc = sum(1 for n, f in zip(nat, fid) if n >= 4 and f <= 2) / len(nat)
        print(
            f"| {LABELS[m]} | {mean(nat):.2f} | {mean(good):.2f} | "
            f"{(mean(bad) if bad else 0):.2f} | {halluc:.0%} | {pearson(nat, fid):.2f} |"
        )


def _check() -> None:
    sample = pick_sample()
    assert len(sample) == N_PER_CAT * len(CATEGORIES) == 50
    assert len(set(sample)) == 50, "sample has duplicates"
    assert sample == pick_sample(), "sample not deterministic"
    data = {m: load_method(m) for m in METHODS}
    for m in METHODS:
        assert len(data[m]) == 150, f"{m}: expected 150 transcripts"
        for sid in sample:
            assert sid in data[m], f"{sid} missing from {m}"
    per_cat: dict[str, int] = {}
    for sid in sample:
        c = data[METHODS[0]][sid]["category"]
        per_cat[c] = per_cat.get(c, 0) + 1
    assert per_cat == {c: N_PER_CAT for c in CATEGORIES}, per_cat
    print("ok:", per_cat)


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif len(sys.argv) > 1 and sys.argv[1] == "sample":
        cmd_sample()
    elif len(sys.argv) > 1 and sys.argv[1] == "agg":
        cmd_agg()
    else:
        print(__doc__)
