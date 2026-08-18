"""Build docs/EVAL.md's reference dataset (eval_set_<date>.jsonl) from data/json + data/wav.

Single-speaker only (li_total_speaker_num == "1") per CLAUDE.md's 1차 목표 — the 30
"일상,소통" 2-speaker clips are skipped until phase 2 (다중화자) opens.

Usage: python scripts/build_eval_set.py
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JSON_DIR = ROOT / "data" / "json"
WAV_DIR = ROOT / "data" / "wav"

# Fields whose presence marks a segment as "hard" per EVAL.md §1
# (filler/repetition/slang/abbreviation/mistake, or a filler recorded in speaker_tone).
HARD_FIELDS = ["sl_new_word", "sl_abbreviation_word", "sl_slang", "sl_mistake", "sl_again"]


def is_hard(d: dict) -> bool:
    if any(d.get(k) for k in HARD_FIELDS):
        return True
    tone = d.get("speaker_tone")
    if isinstance(tone, str):
        tone = json.loads(tone)
    return bool(tone)


def main() -> None:
    records = []
    skipped_multi_speaker = 0
    skipped_no_wav = 0

    for json_path in sorted(JSON_DIR.rglob("*.json")):
        d = json.loads(json_path.read_text(encoding="utf-8"))

        if d.get("li_total_speaker_num") != "1":
            skipped_multi_speaker += 1
            continue

        wav_path = WAV_DIR / json_path.relative_to(JSON_DIR).with_suffix(".wav")
        if not wav_path.exists():
            skipped_no_wav += 1
            continue

        records.append(
            {
                "id": json_path.stem,
                "audio": str(wav_path.relative_to(ROOT)).replace("\\", "/"),
                "ja_ref": d["tc_text"],
                "ko_ref": d["tl_trans_text"],
                "group": "hard" if is_hard(d) else "normal",
                "category": json_path.parent.parent.name,
                "has_proper_noun": bool(d.get("sl_new_word")),
            }
        )

    out_path = ROOT / "data" / f"eval_set_{date.today().isoformat()}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    normal = sum(1 for r in records if r["group"] == "normal")
    hard = sum(1 for r in records if r["group"] == "hard")
    print(f"Wrote {len(records)} segments -> {out_path}")
    print(f"  normal={normal} hard={hard}")
    print(f"  skipped: multi_speaker={skipped_multi_speaker} no_wav={skipped_no_wav}")


if __name__ == "__main__":
    main()
