"""Translate ja_ref -> hyp_ko_from_ref for a dedicated NMT model (NLLB-200 or
MADLAD-400), transformers/GPU, no llama-server involved — these are
encoder-decoder seq2seq models, not chat LLMs. Same output schema as
translate_llm.py so score_chrf.py works unmodified (translate_elapsed_s
included for RTF, same convention as the LLM runner and the STT surveys).

Usage:
    python translate_nmt.py --engine nllb --label nllb-200-3.3b
    python translate_nmt.py --engine madlad --label madlad400-3b-mt
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import torch
from common import load_dataset
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

OUT_ROOT = Path(__file__).resolve().parents[1] / "out"

MODEL_IDS = {
    "nllb": "facebook/nllb-200-3.3B",
    "madlad": "google/madlad400-3b-mt",
}
DEVICE = "cuda"
DTYPE = torch.float16


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=list(MODEL_IDS), required=True)
    parser.add_argument("--label", required=True, help="out/<label>/ subdir name")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    model_id = MODEL_IDS[args.engine]
    segments = load_dataset()
    if args.limit:
        segments = segments[: args.limit]

    print(f"loading {model_id} ({DEVICE}, {DTYPE})...")
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_id, torch_dtype=DTYPE).to(DEVICE)
    model.eval()

    out_dir = OUT_ROOT / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "translations.jsonl"

    t0 = time.monotonic()
    with out_path.open("w", encoding="utf-8") as out_f:
        for i, seg in enumerate(segments, 1):
            t_start = time.monotonic()
            if args.engine == "nllb":
                # NLLB uses FLORES-200 language codes; src_lang set on the
                # tokenizer, forced_bos_token_id picks the target language.
                tokenizer.src_lang = "jpn_Jpan"
                inputs = tokenizer(seg.ja_ref, return_tensors="pt").to(DEVICE)
                forced_bos = tokenizer.convert_tokens_to_ids("kor_Hang")
                with torch.no_grad():
                    output_ids = model.generate(**inputs, forced_bos_token_id=forced_bos, max_new_tokens=200)
                hyp = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0]
            else:  # madlad
                # MADLAD-400 (T5) target language is a literal prefix token
                # in the input text, not a generation kwarg.
                prompt = f"<2ko> {seg.ja_ref}"
                inputs = tokenizer(prompt, return_tensors="pt").to(DEVICE)
                with torch.no_grad():
                    output_ids = model.generate(**inputs, max_new_tokens=200)
                hyp = tokenizer.batch_decode(output_ids, skip_special_tokens=True)[0]
            elapsed = time.monotonic() - t_start

            record = {
                "seg_id": seg.seg_id,
                "category": seg.category,
                "group": seg.group,
                "has_proper_noun": seg.has_proper_noun,
                "ja_ref": seg.ja_ref,
                "ko_ref": seg.ko_ref,
                "duration_s": seg.duration_s,
                "hyp_ko": hyp.strip(),
                "translate_elapsed_s": elapsed,
            }
            out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_f.flush()
            print(f"[{i}/{len(segments)}] {seg.seg_id} t={elapsed:.2f}s -> {hyp.strip()[:40]!r}")

    total = time.monotonic() - t0
    print(f"done: {len(segments)} segments in {total:.1f}s -> {out_path}")


if __name__ == "__main__":
    main()
