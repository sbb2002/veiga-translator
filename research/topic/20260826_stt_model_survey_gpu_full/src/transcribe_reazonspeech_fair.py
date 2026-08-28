"""ReazonSpeech-NeMo-v2 — FAIR re-run via the official `reazonspeech` package
wrapper (VAD + long-form segmentation built in), not bare NeMo transcribe().

Why (report/03-fairness-review.md): the original transcribe_reazonspeech.py
called bare `nemo_asr.models.ASRModel.from_pretrained(...).transcribe([path])`,
which has no VAD / non-speech handling — on noisy/music/silence-padded clips
the RNNT decoder terminated early and emitted 2-4 char fragments, inflating
CER far beyond the model's real error rate. The `reazonspeech.nemo.asr`
wrapper does silero-VAD splitting + per-chunk transcribe + concat.

Runs on CPU (user decision 2026-08-28) — quality is device-independent for
this fp32 deterministic inference; only speed differs. stt_elapsed_s here is
CPU-measured and NOT comparable to report/01's GPU RTF table.

Output schema matches transcribe_turbo.py so score_quantitative.py works
unmodified. Writes to out/reazonspeech-nemo-v2_fair/.

Usage:
  python transcribe_reazonspeech_fair.py [--device cpu] [--limit N]
Run in the `reazonspeech-cpu` conda env (nemo_toolkit[asr] + reazonspeech).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from common import load_dataset

from reazonspeech.nemo.asr import audio_from_path, load_model, transcribe

OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "reazonspeech-nemo-v2_fair"


def _load(device: str):
    """load_model signature has varied across reazonspeech versions; try the
    device kwarg, fall back to a plain load + .to()."""
    try:
        return load_model(device=device)
    except TypeError:
        model = load_model()
        try:
            model = model.to(device)
        except Exception:
            pass
        return model


def _text(ret) -> str:
    if hasattr(ret, "text"):
        return ret.text
    if isinstance(ret, str):
        return ret
    return str(ret)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading reazon-research/reazonspeech-nemo-v2 via wrapper (device={args.device})...")
    model = _load(args.device)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            audio = audio_from_path(str(seg.wav_path))
            t_start = time.monotonic()
            ret = transcribe(model, audio)
            elapsed = time.monotonic() - t_start
            hyp = _text(ret).strip()

            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": hyp,
                "stt_elapsed_s": elapsed,
                "stt_device": args.device,  # CPU marker — report/01 RTF is GPU, not comparable
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s -> {hyp[:48]!r}")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
