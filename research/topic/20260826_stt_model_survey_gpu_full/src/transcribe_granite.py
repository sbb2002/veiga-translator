"""granite-speech-4.1-2b (transformers, GPU) on the full 150-pair set. Same
output schema as transcribe_turbo.py so score_quantitative.py works
unmodified.

API per the model card (ibm-granite/granite-speech-4.1-2b): audio-prompted
chat template + AutoModelForSpeechSeq2Seq.generate. English instruction
("transcribe the speech") is used regardless of ja_ref — the model card's
Japanese support means it transcribes in the spoken language, not that the
*instruction* must be Japanese.

Usage: python transcribe_granite.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import soundfile as sf
import torch
import torchaudio
from common import load_dataset
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

OUT_DIR = Path(__file__).resolve().parents[1] / "out" / "granite-speech-4.1-2b"
MODEL_ID = "ibm-granite/granite-speech-4.1-2b"
DEVICE = "cuda"
DTYPE = torch.float16
USER_PROMPT = "<|audio|>can you transcribe the speech into a written format?"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    # report/03-fairness-review.md: the original pass ran greedy with no
    # repetition control, which is why seg 1373_5567... looped ("あ" x N,
    # CER 5.087) and blew out the 일상,소통 CI. Defaults reproduce that pass
    # exactly (committed out/granite-speech-4.1-2b/). For the fair re-run:
    # --repetition-penalty 1.15 --no-repeat-ngram-size 3 --out-suffix _fair
    parser.add_argument("--repetition-penalty", type=float, default=1.0)
    parser.add_argument("--no-repeat-ngram-size", type=int, default=0)
    parser.add_argument("--out-suffix", default="", help="e.g. _fair -> out/<model><suffix>/")
    args = parser.parse_args()

    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {MODEL_ID} ({DEVICE}, {DTYPE})...")
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    tokenizer = processor.tokenizer
    model = AutoModelForSpeechSeq2Seq.from_pretrained(MODEL_ID, device_map=DEVICE, torch_dtype=DTYPE)
    model.eval()

    chat = [{"role": "user", "content": USER_PROMPT}]
    prompt = tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True)

    out_dir = OUT_DIR.parent / (OUT_DIR.name + args.out_suffix)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "transcripts.jsonl"
    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            data, sr = sf.read(str(seg.wav_path), dtype="float32", always_2d=True)
            wav = torch.from_numpy(data.T)  # (channels, samples)
            if wav.shape[0] > 1:
                wav = wav.mean(dim=0, keepdim=True)
            if sr != 16000:
                wav = torchaudio.functional.resample(wav, sr, 16000)

            t_start = time.monotonic()
            model_inputs = processor(prompt, wav, device=DEVICE, return_tensors="pt").to(DEVICE)
            gen_kwargs = {"max_new_tokens": 256, "do_sample": False}
            if args.repetition_penalty != 1.0:
                gen_kwargs["repetition_penalty"] = args.repetition_penalty
            if args.no_repeat_ngram_size > 0:
                gen_kwargs["no_repeat_ngram_size"] = args.no_repeat_ngram_size
            with torch.no_grad():
                output = model.generate(**model_inputs, **gen_kwargs)
            n_in = model_inputs["input_ids"].shape[-1]
            new_tokens = output[0, n_in:].unsqueeze(0)
            hyp = tokenizer.batch_decode(new_tokens, add_special_tokens=False, skip_special_tokens=True)[0]
            torch.cuda.synchronize()
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
