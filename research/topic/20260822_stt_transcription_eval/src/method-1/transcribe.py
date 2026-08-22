"""Run faster-whisper (large-v3) directly over data/wav+data/json (150 pairs)
and dump raw hypotheses to out/transcripts.jsonl.

Mirrors backend/config.py's FINAL-pass settings (model=large-v3, cuda,
int8_float16, beam_size=5, condition_on_previous_text) but calls
faster_whisper.WhisperModel directly — no backend/ import, no VAD, no
glossary hotwords, no hallucination gate. Each clip is an independent
pre-segmented utterance from the dataset, so condition_on_previous_text is
False here (there is no real "previous" utterance to condition on).

Usage: python transcribe.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from common import load_dataset
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

OUT_PATH = Path(__file__).resolve().parents[2] / "out" / "method-1" / "transcripts.jsonl"

# Mirrors backend/config.py WHISPER_* (final-pass values).
MODEL_SIZE = "large-v3"
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float16"
LANGUAGE = "ja"
BEAM_SIZE = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {MODEL_SIZE} ({DEVICE}, {COMPUTE_TYPE})...")
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)

    OUT_PATH.parent.mkdir(exist_ok=True)
    t0 = time.monotonic()
    with OUT_PATH.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            audio = decode_audio(str(seg.wav_path), sampling_rate=16000)
            t_start = time.monotonic()
            whisper_segments, info = model.transcribe(
                audio,
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                condition_on_previous_text=False,
                initial_prompt=None,
                hotwords=None,
                word_timestamps=False,
                vad_filter=False,
            )
            text_parts = []
            no_speech_probs = []
            avg_logprobs = []
            for s in whisper_segments:
                text_parts.append(s.text)
                no_speech_probs.append(s.no_speech_prob)
                avg_logprobs.append(s.avg_logprob)
            elapsed = time.monotonic() - t_start
            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "wav_path": str(seg.wav_path),
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp": "".join(text_parts).strip(),
                "no_speech_prob": (
                    sum(no_speech_probs) / len(no_speech_probs) if no_speech_probs else None
                ),
                "avg_logprob": sum(avg_logprobs) / len(avg_logprobs) if avg_logprobs else None,
                "stt_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            if i % 10 == 0 or i == len(segments):
                print(f"[{i}/{len(segments)}] {seg.seg_id} stt={elapsed:.2f}s")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {OUT_PATH}")


if __name__ == "__main__":
    main()
