"""reazon-research/reazonspeech-nemo-v2 (NeMo, GPU) on the full 150-pair set.
Same output schema as transcribe_turbo.py so score_quantitative.py works
unmodified.

Added 2026-08-26 (5th candidate) after the user flagged that our own 150-pair
dataset is small enough that its ranking shouldn't be blindly trusted, and
asked to add this candidate specifically — a Fast-Conformer + RNN-T model
trained directly on Japanese TV broadcast audio (ReazonSpeech corpus), the
closest domain match to this project's data (Japanese YouTube live streams)
among all candidates surveyed so far.

Must run in a DEDICATED conda env (`reazonspeech`), not `live-translator` —
nemo_toolkit's dependency resolution force-upgraded torch to a CPU-only 2.13
build when first tried in `live-translator`, breaking CUDA there. Kept
strictly isolated after that.

API per the model card (reazon-research/reazonspeech-nemo-v2):
nemo_asr.models.ASRModel.from_pretrained(...).transcribe([wav_path]).

Usage: python transcribe_reazonspeech.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import soundfile as sf
import torch
import torchaudio
from common import load_dataset

import nemo.collections.asr as nemo_asr

OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "reazonspeech-nemo-v2"
MODEL_ID = "reazon-research/reazonspeech-nemo-v2"


def to_mono_16k_wav(src_path: Path, dst_path: Path) -> None:
    """The model expects (batch, time) mono 16kHz input; our corpus wavs are
    stereo 44.1kHz — resample+downmix to a scratch file per segment (same
    approach as transcribe_granite.py)."""
    data, sr = sf.read(str(src_path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T)  # (channels, samples)
    if wav.shape[0] > 1:
        wav = wav.mean(dim=0, keepdim=True)
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    sf.write(str(dst_path), wav.squeeze(0).numpy(), 16000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {MODEL_ID} (cuda)...")
    model = nemo_asr.models.ASRModel.from_pretrained(MODEL_ID)
    model = model.cuda()
    model.eval()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f, tempfile.TemporaryDirectory() as scratch:
        scratch_path = Path(scratch) / "mono16k.wav"
        for i, seg in enumerate(segments, 1):
            to_mono_16k_wav(seg.wav_path, scratch_path)
            t_start = time.monotonic()
            result = model.transcribe([str(scratch_path)], verbose=False)
            hyp = result[0].text if hasattr(result[0], "text") else result[0]
            elapsed = time.monotonic() - t_start

            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": hyp.strip(),
                "stt_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s -> {hyp.strip()[:40]!r}")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
