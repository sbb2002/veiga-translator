"""Score transcripts using multilingual-e5-large semantic embedding similarity.

Computes cosine similarity between Japanese reference (ja_ref) and hypothesis (hyp),
both treated as Japanese text. Requires sentence-transformers on GPU box; --check
runs without it (logic verification only).

Usage: python score_embedding.py --run <label> --transcripts <path>
       python score_embedding.py --check
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from common import CATEGORIES

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = TOPIC_ROOT / "out"


def _e5_prefix(text: str) -> str:
    """Add e5 instruction prefix (symmetric query: on both sides)."""
    return "query: " + text.strip()


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of unit vectors (dot product)."""
    return float(np.dot(a, b))


def score_run(run_label: str, transcripts_path: Path | str) -> None:
    """Score transcripts with e5 embedding cosine similarity."""
    # Lazy import (may not be installed on dev machine)
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"Error: {e}. Install sentence-transformers and torch.", file=sys.stderr)
        sys.exit(1)

    transcripts_path = Path(transcripts_path)
    if not transcripts_path.exists():
        print(f"Error: {transcripts_path} not found", file=sys.stderr)
        sys.exit(1)

    # Load records
    records = []
    with transcripts_path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    # Detect device
    device = "cuda" if torch.cuda.is_available() else "cpu"

    # Load model
    model = SentenceTransformer("intfloat/multilingual-e5-large", device=device)

    # Prepare refs and hyps (ja_ref always present, hyp may be empty after strip)
    refs = [_e5_prefix(r["ja_ref"]) for r in records]
    hyps_raw = [r["hyp"].strip() if isinstance(r["hyp"], str) else "" for r in records]
    empty_mask = np.array([h == "" for h in hyps_raw])
    hyps_prefixed = [_e5_prefix(h) for h in hyps_raw if h != ""]

    # Encode (batch for speed)
    ref_embeddings = model.encode(refs, batch_size=32, normalize_embeddings=True)
    hyp_embeddings_non_empty = (
        model.encode(hyps_prefixed, batch_size=32, normalize_embeddings=True)
        if hyps_prefixed
        else np.array([])
    )

    # Splice zeros back in for empty hyps
    hyp_embeddings = np.zeros_like(ref_embeddings)
    non_empty_idx = 0
    for i, is_empty in enumerate(empty_mask):
        if not is_empty:
            hyp_embeddings[i] = hyp_embeddings_non_empty[non_empty_idx]
            non_empty_idx += 1

    # Compute cosine similarities (dot product of normalized vectors)
    cos_sims = np.array([_cosine_sim(ref_embeddings[i], hyp_embeddings[i]) for i in range(len(records))])

    # Per-segment output
    per_seg = []
    for i, r in enumerate(records):
        per_seg.append({
            "seg_id": r["seg_id"],
            "category": r["category"],
            "cos_sim": round(float(cos_sims[i]), 4),
        })

    # Summary
    summary = {
        "overall": {
            "n": len(records),
            "mean_cos_sim": round(float(np.mean(cos_sims)), 4),
        },
        "_empty_hyp": int(np.sum(empty_mask)),
    }
    for cat in CATEGORIES:
        cat_indices = [i for i, r in enumerate(records) if r["category"] == cat]
        if cat_indices:
            cat_sims = cos_sims[cat_indices]
            summary[cat] = {
                "n": len(cat_indices),
                "mean_cos_sim": round(float(np.mean(cat_sims)), 4),
            }

    # Write output
    out_dir = OUT_ROOT / run_label
    out_dir.mkdir(parents=True, exist_ok=True)

    per_seg_path = out_dir / "embedding_per_segment.jsonl"
    with per_seg_path.open("w", encoding="utf-8") as f:
        for row in per_seg:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_path = out_dir / "embedding_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nper-segment -> {per_seg_path}")
    print(f"summary -> {summary_path}")


def check() -> None:
    """Verify math without sentence-transformers."""
    # Test 1: unit vector with itself == 1.0
    v = np.array([1.0, 0.0, 0.0])
    v_norm = v / np.linalg.norm(v)
    sim = _cosine_sim(v_norm, v_norm)
    assert abs(sim - 1.0) < 1e-6, f"self-sim != 1.0: {sim}"

    # Test 2: unit vector with its negation == -1.0
    neg_v = -v_norm
    sim = _cosine_sim(v_norm, neg_v)
    assert abs(sim - (-1.0)) < 1e-6, f"neg-sim != -1.0: {sim}"

    # Test 3: e5 prefix
    result = _e5_prefix("  abc ")
    assert result == "query: abc", f"_e5_prefix failed: '{result}'"

    # Test 4: empty hyp masking logic
    # Simulate: hyps_raw=[h1, "", h3], where h1 and h3 are non-empty
    hyps_raw = ["h1", "", "h3"]
    empty_mask = np.array([h == "" for h in hyps_raw])
    hyps_prefixed = [_e5_prefix(h) for h in hyps_raw if h != ""]

    # Simulate model returning random normalized embeddings
    ref_embeddings = np.random.randn(3, 768)
    ref_embeddings /= np.linalg.norm(ref_embeddings, axis=1, keepdims=True)
    hyp_embeddings_non_empty = np.random.randn(len(hyps_prefixed), 768)
    hyp_embeddings_non_empty /= np.linalg.norm(hyp_embeddings_non_empty, axis=1, keepdims=True)

    # Splice: position 1 (empty) should be all zeros
    hyp_embeddings = np.zeros_like(ref_embeddings)
    non_empty_idx = 0
    for i, is_empty in enumerate(empty_mask):
        if not is_empty:
            hyp_embeddings[i] = hyp_embeddings_non_empty[non_empty_idx]
            non_empty_idx += 1

    # Verify masking
    assert np.allclose(hyp_embeddings[1], 0.0), "empty mask failed: index 1 not zero"
    assert not np.allclose(hyp_embeddings[0], 0.0), "empty mask failed: index 0 is zero"
    assert not np.allclose(hyp_embeddings[2], 0.0), "empty mask failed: index 2 is zero"

    # Verify cosine sim for empty hyp is exactly 0.0
    cos_sims = np.array([_cosine_sim(ref_embeddings[i], hyp_embeddings[i]) for i in range(3)])
    assert cos_sims[1] == 0.0, f"empty hyp cos_sim != 0.0: {cos_sims[1]}"

    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", help="run label (e.g., A_qwen3-asr-1.7b, B_turbo)")
    parser.add_argument("--transcripts", type=str, help="path to transcripts JSONL file")
    parser.add_argument("--check", action="store_true", help="run logic checks (no sentence-transformers needed)")
    args = parser.parse_args()

    if args.check:
        check()
    elif args.run and args.transcripts:
        score_run(args.run, args.transcripts)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
