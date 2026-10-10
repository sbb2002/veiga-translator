"""Compare the torch and onnx VAD backends on the same frames.

Usage (from repo root):
    python scripts/verify_vad_parity.py

Reads 20 wav clips (4 per category) from data/wav/, converts each to 16kHz
mono float32, splits into config.VAD_FRAME_SAMPLES frames, and reports:
  (a) max absolute difference of speech_probability between backends,
  (b) is_speech (threshold config.VAD_SPEECH_THRESHOLD) agreement rate,
  (c) mean processing time per frame for each backend.
reset() is called at the start of every clip for both backends.
"""

from __future__ import annotations

import glob
import math
import os
import sys
import time

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from backend import config  # noqa: E402
from backend.vad import SileroVAD  # noqa: E402

# data/wav is gitignored, so it may only exist in the main checkout; override with VAD_WAV_ROOT.
WAV_ROOT = os.environ.get("VAD_WAV_ROOT") or os.path.join(REPO_ROOT, "data", "wav")
CLIPS_PER_CATEGORY = 4
TARGET_SR = 16000


def select_clips() -> list[str]:
    """Deterministic pick: first CLIPS_PER_CATEGORY wavs (sorted) from each category dir."""
    clips: list[str] = []
    for category in sorted(os.listdir(WAV_ROOT)):
        cat_dir = os.path.join(WAV_ROOT, category)
        if not os.path.isdir(cat_dir):
            continue
        wavs = sorted(glob.glob(os.path.join(cat_dir, "**", "*.wav"), recursive=True))
        clips.extend(wavs[:CLIPS_PER_CATEGORY])
    return clips


def load_16k_mono(path: str) -> np.ndarray:
    data, sr = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if sr != TARGET_SR:
        g = math.gcd(sr, TARGET_SR)
        mono = resample_poly(mono, TARGET_SR // g, sr // g).astype(np.float32)
    return mono


def run_backend(backend: str, clips: list[np.ndarray], frame_len: int):
    config.VAD_BACKEND = backend
    vad = SileroVAD()
    probs_per_clip: list[np.ndarray] = []
    total_time = 0.0
    total_frames = 0
    for clip in clips:
        vad.reset()
        n = len(clip) // frame_len
        probs = np.empty(n, dtype=np.float64)
        t0 = time.perf_counter()
        for i in range(n):
            probs[i] = vad.speech_probability(clip[i * frame_len : (i + 1) * frame_len])
        total_time += time.perf_counter() - t0
        total_frames += n
        probs_per_clip.append(probs)
    mean_ms = 1000.0 * total_time / max(total_frames, 1)
    return probs_per_clip, mean_ms


def main() -> int:
    frame_len = config.VAD_FRAME_SAMPLES
    threshold = config.VAD_SPEECH_THRESHOLD
    paths = select_clips()
    if len(paths) < 20:
        print(f"warning: only {len(paths)} clips found under {WAV_ROOT}")
    clips = [load_16k_mono(p) for p in paths]
    total_frames = sum(len(c) // frame_len for c in clips)
    print(f"clips={len(clips)} frames={total_frames} frame_len={frame_len} threshold={threshold}")

    torch_probs, torch_ms = run_backend("torch", clips, frame_len)
    onnx_probs, onnx_ms = run_backend("onnx", clips, frame_len)

    max_abs = 0.0
    agree = 0
    total = 0
    for tp, op in zip(torch_probs, onnx_probs):
        if len(tp) == 0:
            continue
        max_abs = max(max_abs, float(np.max(np.abs(tp - op))))
        agree += int(np.sum((tp >= threshold) == (op >= threshold)))
        total += len(tp)
    agreement = 100.0 * agree / max(total, 1)

    print(f"(a) max_abs_error = {max_abs:.6e}")
    print(f"(b) is_speech agreement = {agree}/{total} = {agreement:.3f}%")
    print(f"(c) mean time/frame: torch = {torch_ms:.4f} ms, onnx = {onnx_ms:.4f} ms")
    ok = max_abs < 1e-3 or agreement >= 99.0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
