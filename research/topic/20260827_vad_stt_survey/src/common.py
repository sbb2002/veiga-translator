"""Shared dataset loading + text normalization for the VAD-STT gap survey.

Dataset loader is a near-copy of
`20260826_stt_model_survey_gpu_full/src/common.py` (same 150-pair set, same
`data/json` + `data/wav` layout) so this topic stays self-contained. The one
intentional convention break for this topic lives in `run_pipeline.py`, which
DOES import `backend.*` (it has to — it tests the real pipeline). Everything
else here is standalone.

`normalize_ja` matches EVAL.md §2.1 and the prior STT surveys exactly: NFKC,
then strip the punctuation set + whitespace, applied identically to reference
and hypothesis before any quantitative metric. Punctuation adherence is judged
only in the qualitative pass (report/02), never in CER/chrF++/BLEU/ROUGE-L.
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
    assert len(segs) == 150, len(segs)
    assert set(by_cat.values()) == {30}, by_cat
    print("ok")
