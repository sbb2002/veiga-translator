"""Shared dataset loading for the translation-model survey. Standalone from
the live-translator app on purpose (same convention as the STT surveys):
evaluates translation models directly, no `backend.*` imports except where a
script specifically needs LlamaServerEngine to talk to a running llama-server.

Uses the project's existing curated 120-clip eval set
(data/eval_set_2026-08-18.jsonl, ja_ref/ko_ref/group/category/has_proper_noun)
rather than the raw 150-pair corpus the STT surveys used — this is the
project's established translation-eval dataset (docs/eval/EVAL.md), already
used for every prior translation-model benchmark in this repo, so reusing it
keeps this survey's numbers comparable to that history. duration_s (needed
for RTF, which eval_set_2026-08-18.jsonl itself doesn't carry) is
cross-referenced from the matching data/json/<category>/.../<id>.json used by
the STT surveys — same id, same underlying corpus.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
EVAL_SET_PATH = REPO_ROOT / "data" / "eval_set_2026-08-18.jsonl"
DATA_ROOT = REPO_ROOT / "data"


@dataclass
class Segment:
    seg_id: str
    category: str
    group: str  # "normal" | "hard"
    ja_ref: str
    ko_ref: str
    has_proper_noun: bool
    duration_s: float


def load_dataset() -> list[Segment]:
    segments: list[Segment] = []
    with EVAL_SET_PATH.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            audio_path = Path(r["audio"])
            json_path = DATA_ROOT / "json" / r["category"] / audio_path.parent.name / (r["id"] + ".json")
            assert json_path.exists(), f"missing metadata json for {r['id']}"
            meta = json.loads(json_path.read_text(encoding="utf-8"))
            segments.append(
                Segment(
                    seg_id=r["id"],
                    category=r["category"],
                    group=r["group"],
                    ja_ref=r["ja_ref"],
                    ko_ref=r["ko_ref"],
                    has_proper_noun=r["has_proper_noun"],
                    duration_s=float(meta["fi_duration_time"]),
                )
            )
    return segments


if __name__ == "__main__":
    segs = load_dataset()
    by_group: dict[str, int] = {}
    for s in segs:
        by_group[s.group] = by_group.get(s.group, 0) + 1
    print(f"loaded {len(segs)} segments, {by_group}")
