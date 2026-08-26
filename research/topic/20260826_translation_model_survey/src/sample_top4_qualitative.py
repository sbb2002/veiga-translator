"""Pull a fixed random sample of segments and print ja_ref/ko_ref alongside
the top-4 statistically-tied methods' (per report/03) hyp_ko, for manual
qualitative comparison — chrF++ can't distinguish them (95% CIs all overlap),
so this is the tie-breaker read requested by the user (2026-08-27).

Usage: python sample_top4_qualitative.py > ../out/top4_qualitative_sample.txt
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"
METHODS = ["gemma-3-12b-it", "qwen3-14b", "qwen3-32b", "exaone-3.5-7.8b"]
SAMPLE_N = 20
SEED = 7


def main() -> None:
    data = {}
    for m in METHODS:
        recs = [json.loads(line) for line in (OUT_ROOT / m / "translations.jsonl").open(encoding="utf-8")]
        data[m] = {r["seg_id"]: r for r in recs}

    ids = list(data[METHODS[0]].keys())
    random.seed(SEED)
    sample = random.sample(ids, SAMPLE_N)

    for seg_id in sample:
        r0 = data[METHODS[0]][seg_id]
        print(f"=== {seg_id} ({r0['group']}, {r0['category']}) ===")
        print(f"JA : {r0['ja_ref']}")
        print(f"REF: {r0['ko_ref']}")
        for m in METHODS:
            print(f"{m:16s}: {data[m][seg_id]['hyp_ko']}")
        print()


if __name__ == "__main__":
    main()
