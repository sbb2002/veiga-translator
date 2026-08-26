"""Shared dataset loading + text normalization for the GPU full-set ASR model
survey. Standalone from the live-translator app on purpose (same convention as
20260822_stt_transcription_eval / 20260826_stt_model_survey): evaluates STT
models directly, no `backend.*` imports.

Unlike 20260826_stt_model_survey (25-segment CPU pilot), this topic always
loads the full 150-pair set — GPU is available (2026-08-26), so there's no
need for a CPU-speed-driven subset.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = REPO_ROOT / "data"
CATEGORIES = ["게임", "여행", "음식,요리", "일상,소통", "패션,뷰티"]

# EVAL.md §2.1: NFKC normalize, strip this punctuation set + whitespace,
# applied identically to ja_ref and hypothesis before scoring.
_PUNCT_RE = re.compile(r"[、。！？「」『』・…\s]")


def normalize_ja(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return _PUNCT_RE.sub("", text)


@dataclass
class Segment:
    seg_id: str
    category: str
    wav_path: Path
    ja_ref: str
    ko_ref: str
    duration_s: float
    speaker_gender: str
    contents_name: str


def load_dataset() -> list[Segment]:
    segments: list[Segment] = []
    for cat in CATEGORIES:
        json_dir = DATA_ROOT / "json" / cat
        json_paths = sorted(json_dir.rglob("*.json"))
        for json_path in json_paths:
            with json_path.open(encoding="utf-8") as f:
                meta = json.load(f)
            wav_path = DATA_ROOT / "wav" / cat / json_path.parent.name / (json_path.stem + ".wav")
            assert wav_path.exists(), f"missing wav for {json_path}"
            segments.append(
                Segment(
                    seg_id=json_path.stem,
                    category=cat,
                    wav_path=wav_path,
                    ja_ref=meta["tc_text"],
                    ko_ref=meta["tl_trans_text"],
                    duration_s=float(meta["fi_duration_time"]),
                    speaker_gender=meta.get("speaker_gender", ""),
                    contents_name=meta.get("contentsName", ""),
                )
            )
    return segments


if __name__ == "__main__":
    segs = load_dataset()
    by_cat: dict[str, int] = {}
    for s in segs:
        by_cat[s.category] = by_cat.get(s.category, 0) + 1
    print(f"loaded {len(segs)} segments, {by_cat}")
