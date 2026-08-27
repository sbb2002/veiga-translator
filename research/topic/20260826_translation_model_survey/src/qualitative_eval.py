"""Manual qualitative eval for the translation-model survey — a human (Claude,
2026-08-27) reads a stratified 50-segment sample and scores every method's
ko translation on three 1-5 axes, then this script aggregates.

Extends report/03's 20-segment top-4 read to all 10 methods, 50 segments,
scored (not just failure-pattern prose) — same shape as the STT survey's
`20260826_stt_model_survey_gpu_full/src/qualitative_eval.py`.

Three axes (user request 2026-08-27), each 1 (전혀 그렇지 않음) .. 5 (아주 그러함):
  - fid  의미 충실도: is ja_ref's meaning faithfully conveyed? (ko_ref used only
    to disambiguate ja_ref, never scored on its own)
  - flu  유창성: is the Korean itself fluent/natural? — scored independently of
    correctness, so a fluent mistranslation still scores high here (this is the
    axis that fluent hallucination inflates, cf. STT survey's "naturalness")
  - nua  뉘앙스 이전: is the JA register / idiom / politeness / wordplay carried
    into an equivalent KO nuance (not just a literal gloss)?

Derived: 유창한 환각률 = flu >= 4 & fid <= 2 (confidently-wrong output).

Scope: ALL 120 segments of the eval set (data/eval_set_2026-08-18.jsonl),
every one of the 10 methods — paired, no sampling (user request 2026-08-27:
"전부 다 해").

Usage:
  python qualitative_eval.py sample   # (re)write out/qualitative_sample.txt
  python qualitative_eval.py agg      # aggregate out/qualitative_scores.json
  python qualitative_eval.py --check
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"

# quality order (report/02, report/03): 4-clique + old baseline + bridge, then
# the 4 statistically-separated failures.
METHODS = [
    "gemma-3-12b-it", "qwen3-14b", "qwen3-32b", "exaone-3.5-7.8b",
    "qwen2.5-7b-baseline", "llama-3-8b-instruct",
    "seed-x-instruct-7b", "nllb-200-3.3b", "exaone-4.0-32b", "madlad400-3b-mt",
]
LABELS = {m: m for m in METHODS}
CATEGORIES = ["게임", "음식,요리", "일상,소통", "패션,뷰티"]
SAMPLE_TXT = OUT_ROOT / "qualitative_sample.txt"
SCORES_JSON = OUT_ROOT / "qualitative_scores.json"
AXES = ["fid", "flu", "nua"]


def load_method(method: str) -> dict[str, dict]:
    path = OUT_ROOT / method / "translations.jsonl"
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    return {r["seg_id"]: r for r in rows}


def pick_sample() -> list[str]:
    """All 120 seg_ids, ordered by category then seg_id — deterministic, no sampling."""
    base = load_method(METHODS[0])
    by_cat: dict[str, list[str]] = {c: [] for c in CATEGORIES}
    for seg_id, r in base.items():
        by_cat[r["category"]].append(seg_id)
    picked: list[str] = []
    for cat in CATEGORIES:
        picked.extend(sorted(by_cat[cat]))
    return picked


def cmd_sample() -> None:
    sample = pick_sample()
    data = {m: load_method(m) for m in METHODS}
    lines: list[str] = []
    for i, seg_id in enumerate(sample, 1):
        base = data[METHODS[0]][seg_id]
        lines.append(f"### [{i:02d}] {seg_id}  ({base['category']} / {base['group']})")
        lines.append(f"JA_REF : {base['ja_ref']}")
        lines.append(f"KO_REF : {base['ko_ref']}")
        for m in METHODS:
            lines.append(f"  {LABELS[m]:<20}: {data[m][seg_id]['hyp_ko']}")
        lines.append("")
    SAMPLE_TXT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(sample)} segments, {len(METHODS)} methods -> {SAMPLE_TXT}")


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    return cov / (sx * sy) if sx and sy else float("nan")


def cmd_agg() -> None:
    """qualitative_scores.json shape:
    {"<seg_id>": {"<method>": {"fid": 1-5, "flu": 1-5, "nua": 1-5}, ...}, ...}
    """
    scores = json.loads(SCORES_JSON.read_text(encoding="utf-8"))
    sample = pick_sample()
    data = {m: load_method(m) for m in METHODS}
    cat_of = {sid: data[METHODS[0]][sid]["category"] for sid in sample}
    grp_of = {sid: data[METHODS[0]][sid]["group"] for sid in sample}

    assert set(scores) == set(sample), (
        f"scored != sample: missing {set(sample) - set(scores)}, extra {set(scores) - set(sample)}"
    )
    for sid, per_method in scores.items():
        assert set(per_method) == set(METHODS), f"{sid}: methods {set(per_method)}"
        for m, s in per_method.items():
            for ax in AXES:
                assert 1 <= s[ax] <= 5, f"{sid}/{m}/{ax}: {s[ax]}"

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs)

    print(f"### 전체 평균 (n={len(sample)})\n")
    print("| 방법 | 의미 충실도 | 유창성 | 뉘앙스 이전 | 유창한 환각률* |")
    print("|---|---|---|---|---|")
    for m in METHODS:
        fid = [scores[s][m]["fid"] for s in sample]
        flu = [scores[s][m]["flu"] for s in sample]
        nua = [scores[s][m]["nua"] for s in sample]
        h = sum(1 for s in sample if scores[s][m]["flu"] >= 4 and scores[s][m]["fid"] <= 2)
        print(f"| {LABELS[m]} | {mean(fid):.2f} | {mean(flu):.2f} | {mean(nua):.2f} | {h}/{len(sample)} ({h/len(sample):.0%}) |")
    print("\n\\* 유창성 ≥ 4 & 의미 충실도 ≤ 2 = 유창하지만 내용이 틀린 번역\n")

    for ax, title in [("fid", "의미 충실도"), ("flu", "유창성"), ("nua", "뉘앙스 이전")]:
        print(f"### 카테고리별 {title} 평균\n")
        print("| 카테고리 | " + " | ".join(LABELS[m] for m in METHODS) + " |")
        print("|---|" + "---|" * len(METHODS))
        for cat in CATEGORIES:
            sids = [s for s in sample if cat_of[s] == cat]
            cells = [f"{mean([scores[s][m][ax] for s in sids]):.2f}" for m in METHODS]
            print(f"| {cat} (n={len(sids)}) | " + " | ".join(cells) + " |")
        print()

    hard = [s for s in sample if grp_of[s] == "hard"]
    print(f"### hard 그룹만 (n={len(hard)})\n")
    print("| 방법 | 의미 충실도 | 유창성 | 뉘앙스 이전 |")
    print("|---|---|---|---|")
    for m in METHODS:
        print(f"| {LABELS[m]} | " + " | ".join(f"{mean([scores[s][m][ax] for s in hard]):.2f}" for ax in AXES) + " |")

    print("\n### 의미 충실도 점수 분포 (1 / 2 / 3 / 4 / 5)\n")
    print("| 방법 | 1 | 2 | 3 | 4 | 5 |")
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        dist = [sum(1 for s in sample if scores[s][m]["fid"] == k) for k in range(1, 6)]
        print(f"| {LABELS[m]} | " + " | ".join(str(d) for d in dist) + " |")

    print("\n### 축 간 상관 (세그먼트 단위, 방법 내부) — 간섭 확인\n")
    print("| 방법 | r(충실,유창) | r(충실,뉘앙스) | r(유창,뉘앙스) |")
    print("|---|---|---|---|")
    for m in METHODS:
        fid = [scores[s][m]["fid"] for s in sample]
        flu = [scores[s][m]["flu"] for s in sample]
        nua = [scores[s][m]["nua"] for s in sample]
        print(f"| {LABELS[m]} | {_pearson(fid, flu):.2f} | {_pearson(fid, nua):.2f} | {_pearson(flu, nua):.2f} |")

    # pooled across all methods x segments
    F = [scores[s][m]["fid"] for m in METHODS for s in sample]
    L = [scores[s][m]["flu"] for m in METHODS for s in sample]
    N = [scores[s][m]["nua"] for m in METHODS for s in sample]
    print(f"\npooled (n={len(F)}): r(충실,유창)={_pearson(F, L):.2f}  r(충실,뉘앙스)={_pearson(F, N):.2f}  r(유창,뉘앙스)={_pearson(L, N):.2f}")

    # method-level: mean flu vs halluc rate (parallels STT survey)
    mflu = [mean([scores[s][m]["flu"] for s in sample]) for m in METHODS]
    hall = [sum(1 for s in sample if scores[s][m]["flu"] >= 4 and scores[s][m]["fid"] <= 2) / len(sample) for m in METHODS]
    print(f"method-level (n={len(METHODS)}): r(평균 유창성, 유창한 환각률) = {_pearson(mflu, hall):.2f}")

    print("\n### 유창성 분해 (유창한 환각 오염 확인)\n")
    print("| 방법 | 유창 전체 | 유창(충실≥3) | 유창(충실≤2) | 유창한 환각률 | r(충실,유창) |")
    print("|---|---|---|---|---|---|")
    for m in METHODS:
        fid = [scores[s][m]["fid"] for s in sample]
        flu = [scores[s][m]["flu"] for s in sample]
        good = [l for l, f in zip(flu, fid) if f >= 3]
        bad = [l for l, f in zip(flu, fid) if f <= 2]
        h = sum(1 for l, f in zip(flu, fid) if l >= 4 and f <= 2) / len(flu)
        print(f"| {LABELS[m]} | {mean(flu):.2f} | {(mean(good) if good else 0):.2f} | {(mean(bad) if bad else 0):.2f} | {h:.0%} | {_pearson(fid, flu):.2f} |")


def _check() -> None:
    sample = pick_sample()
    assert len(sample) == 120, len(sample)
    assert len(set(sample)) == 120, "duplicates"
    assert sample == pick_sample(), "not deterministic"
    data = {m: load_method(m) for m in METHODS}
    for m in METHODS:
        assert len(data[m]) == 120, f"{m}: expected 120"
        for sid in sample:
            assert sid in data[m], f"{sid} missing from {m}"
    per_cat: dict[str, int] = {}
    for sid in sample:
        c = data[METHODS[0]][sid]["category"]
        per_cat[c] = per_cat.get(c, 0) + 1
    n_hard = sum(1 for sid in sample if data[METHODS[0]][sid]["group"] == "hard")
    print(f"ok: {per_cat}, hard={n_hard}")


if __name__ == "__main__":
    if "--check" in sys.argv:
        _check()
    elif len(sys.argv) > 1 and sys.argv[1] == "sample":
        cmd_sample()
    elif len(sys.argv) > 1 and sys.argv[1] == "agg":
        cmd_agg()
    else:
        print(__doc__)
