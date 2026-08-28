"""Offline harness feeding real AudioSession through production pipeline.

THIS IS AN INTENTIONAL EXCEPTION to the "no backend.* imports in research"
convention — this script must test the real pipeline code, not a reimplementation.
Every other research script stays standalone; this one imports backend.audio_session,
backend.stt, backend.vad, backend.translation, etc. directly. Run from repo root
so absolute imports resolve.

Design: DESIGN.md §5 (harness), §6 (transcript assembly).

Run from the repo root (so `backend.*` resolves — the script also inserts
the repo root and its own src/ dir onto sys.path as a fallback):

CLI:
  python research/topic/20260827_vad_std_survey/src/run_pipeline.py \\
    --engine {qwen3-asr-1.7b|turbo} [--limit N] [--realtime] \\
    [--compare-clocks] [--check]

  --engine: STT model (required unless --check).
  --limit N: process first N clips only.
  --realtime: use real asyncio.sleep (not patched virtual clock). Output file
    gets _realtime suffix.
  --compare-clocks: run each of first N clips both ways (virtual + realtime),
    compute normalized CER on hyp texts, write out/clock_validation.json with
    per-segment deltas and aggregate stats. Pass/fail if max_cer <= 0.01.
  --check: self-check (no GPU, no backend imports). Validates chunking, float32
    <-> PCM16 round-trip, assemble_record logic, and the finalize-trigger regex.
    Prints 'ok' and exits 0 on success.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

# Fallback for imports from research subdirectory context.
repo_root = Path(__file__).resolve().parents[4]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

# Add src directory to path so we can import common and virtual_clock directly.
src_dir = Path(__file__).resolve().parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from common import CATEGORIES, load_dataset, normalize_ja
from virtual_clock import VirtualClock, patched_clock

TOPIC_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = TOPIC_ROOT / "out"

SAMPLE_RATE = 16000
CHUNK_SAMPLES = int(SAMPLE_RATE * 0.3)  # 0.3s chunks


# ============================================================================
# STT Timing Proxy (measures transcribe call time, thread-safe)
# ============================================================================


class TimingSTT:
    """Wraps any STTEngine, measuring wall-clock time per transcribe call.
    Thread-safe: STT calls come via asyncio.to_thread, appends guarded by lock.
    """

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.calls: list[tuple[bool, float]] = []  # (fast, elapsed_s)
        self._lock = threading.Lock()

    def warmup(self) -> None:
        self._engine.warmup()

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        fast: bool = False,
        previous_context: str | None = None,
    ) -> Any:
        start = time.perf_counter()
        result = self._engine.transcribe(
            audio, fast=fast, previous_context=previous_context
        )
        elapsed = time.perf_counter() - start
        with self._lock:
            self.calls.append((fast, elapsed))
        return result

    def reset(self) -> None:
        """Clear accumulated timing data before processing a new clip."""
        with self._lock:
            self.calls.clear()


# ============================================================================
# Null Translation Engine (no-op, accepts arbitrary kwargs from AudioSession)
# ============================================================================


class NullTranslation:
    """Async translation engine that returns empty text, swallowing all kwargs
    that AudioSession._do_finalize passes (context, context_translation,
    glossary_hint, broadcaster_hint, allowed_literals).
    """

    async def translate(
        self, text: str, *, fast: bool = False, **kwargs
    ) -> Any:
        """Return empty translation, accept and ignore all extra kwargs."""
        # Lazy import here to avoid torch at module load time.
        from backend.translation.base import TranslationResult

        return TranslationResult(text="")

    async def translate_ko_to_ja(self, text: str, *, context: str | None = None, **kwargs) -> Any:
        from backend.translation.base import TranslationResult

        return TranslationResult(text="")

    async def summarize_context(self, ja_history: str) -> str:
        return ""

    async def context_changed(self, current_summary: str, recent_ja: str) -> bool:
        return False


# ============================================================================
# Output Assembly (pure function, testable independently)
# ============================================================================


def assemble_record(
    seg: Any,
    events: list[dict[str, Any]],
    timing_calls: list[tuple[bool, float]],
    reason_counts: dict[str, int],
) -> dict[str, Any]:
    """Assemble the final output record for a clip from pipeline components.

    Args:
        seg: Segment (has seg_id, category, duration_s, ja_ref, ko_ref)
        events: list of dicts emitted by AudioSession.on_event
        timing_calls: list of (fast: bool, elapsed_s: float) tuples from TimingSTT
        reason_counts: dict with keys {hard_cap, grace_expired, silence_complete,
                        strong_boundary}, values are counts (0 if not present)

    Returns:
        dict with keys: seg_id, category, duration_s, ja_ref, ko_ref, hyp,
                        n_finals, n_final_events, n_dropped_finals, n_partial_calls,
                        stt_elapsed_s, stt_elapsed_s_final, stt_elapsed_s_partial,
                        finalize_reason_counts
    """
    final_events = [e for e in events if e.get("type") == "final"]
    nonempty = [e for e in final_events if (e.get("text") or "").strip()]
    hyp = " ".join((e["text"]).strip() for e in nonempty)

    stt_elapsed_s_partial = sum(el for f, el in timing_calls if f)
    stt_elapsed_s_final = sum(el for f, el in timing_calls if not f)
    stt_elapsed_s = stt_elapsed_s_partial + stt_elapsed_s_final
    n_partial_calls = sum(1 for f, _ in timing_calls if f)
    n_dropped_finals = len(final_events) - len(nonempty)

    # Ensure all four reason keys exist in result, missing -> 0.
    normalized_reasons = {
        "hard_cap": reason_counts.get("hard_cap", 0),
        "grace_expired": reason_counts.get("grace_expired", 0),
        "silence_complete": reason_counts.get("silence_complete", 0),
        "strong_boundary": reason_counts.get("strong_boundary", 0),
    }

    return {
        "seg_id": seg.seg_id,
        "category": seg.category,
        "duration_s": seg.duration_s,
        "ja_ref": seg.ja_ref,
        "ko_ref": seg.ko_ref,
        "hyp": hyp,
        "n_finals": len(nonempty),
        "n_final_events": len(final_events),
        "n_dropped_finals": n_dropped_finals,
        "n_partial_calls": n_partial_calls,
        "stt_elapsed_s": stt_elapsed_s,
        "stt_elapsed_s_final": stt_elapsed_s_final,
        "stt_elapsed_s_partial": stt_elapsed_s_partial,
        "finalize_reason_counts": normalized_reasons,
    }


# ============================================================================
# Per-Clip Processing (async)
# ============================================================================


async def process_clip(
    seg: Any,
    engine_proxy: TimingSTT,
    vad: Any,
    clock_or_none: VirtualClock | None,
    realtime: bool,
) -> dict[str, Any]:
    """Process one audio clip through the real AudioSession pipeline.

    Args:
        seg: Segment with wav_path, duration_s, etc.
        engine_proxy: TimingSTT wrapping the real STT engine
        vad: SileroVAD instance (shared, reset per clip in AudioSession)
        clock_or_none: VirtualClock if virtual mode, None if realtime
        realtime: True if using real asyncio.sleep, False if virtual clock

    Returns:
        dict with assembled record + timing/reason info
    """
    import soundfile

    # Load and resample audio.
    audio, sr = soundfile.read(str(seg.wav_path), dtype="float32")
    if audio.ndim > 1:  # stereo -> mono
        audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly

        audio = resample_poly(audio, SAMPLE_RATE, sr)
    # resample_poly and mean() can promote to float64 — feed_audio only cares
    # about the values, but keep the STT engines fed exactly what production
    # sends them (float32 mono).
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    assert audio.ndim == 1, f"audio shape: {audio.shape}"

    # Event collection.
    events: list[dict[str, Any]] = []

    async def collector(ev: dict[str, Any]) -> None:
        events.append(dict(ev))

    engine_proxy.reset()

    # Log handler to tally finalize reasons.
    reason_counts: dict[str, int] = {
        "hard_cap": 0,
        "grace_expired": 0,
        "silence_complete": 0,
        "strong_boundary": 0,
    }
    finalize_trigger_re = re.compile(r"finalize trigger=(\w+)")

    class ReasonTallyHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            msg = record.getMessage()
            m = finalize_trigger_re.search(msg)
            if m:
                reason = m.group(1)
                if reason in reason_counts:
                    reason_counts[reason] += 1

    handler = ReasonTallyHandler()
    logger = logging.getLogger("live-translator.backend")
    # audio_session logs the finalize trigger at INFO. Nothing configures
    # logging in this standalone harness, so the logger's effective level is
    # the root default (WARNING) and those records would never reach the
    # handler — leaving finalize_reason_counts silently all-zero. Force INFO
    # for the duration of the clip.
    prev_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # Lazy import AudioSession and associated classes.
        from backend.audio_session import AudioSession

        # Create session inside the running loop.
        session = AudioSession(
            stt_engine=engine_proxy,
            translation_engine=NullTranslation(),
            on_event=collector,
            vad=vad,
            glossary=None,
        )

        # Chunk the audio and feed it.
        for start_idx in range(0, len(audio), CHUNK_SAMPLES):
            end_idx = min(start_idx + CHUNK_SAMPLES, len(audio))
            chunk = audio[start_idx:end_idx]

            # Convert to PCM16 bytes.
            pcm16 = (np.clip(chunk, -1.0, 1.0) * 32768.0).astype("<i2").tobytes()

            # Advance clock or sleep before feeding.
            chunk_duration = len(chunk) / SAMPLE_RATE
            if clock_or_none is not None:
                clock_or_none.advance(chunk_duration)
            elif realtime:
                await asyncio.sleep(chunk_duration)

            await session.feed_audio(pcm16)

        # Finalize.
        await session.close()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev_level)

    # Assemble output.
    record = assemble_record(seg, events, engine_proxy.calls, reason_counts)
    return record


# ============================================================================
# Build Engines
# ============================================================================


def build_engine(name: str) -> Any:
    """Construct the STT engine by name, lazy-importing backend.stt.

    Args:
        name: 'qwen3-asr-1.7b' or 'turbo'

    Returns:
        The engine instance (not wrapped in TimingSTT yet).
    """
    if name == "qwen3-asr-1.7b":
        from backend.stt.qwen3_asr_engine import Qwen3ASREngine

        return Qwen3ASREngine()
    elif name == "turbo":
        from backend.stt.faster_whisper_engine import FasterWhisperEngine

        return FasterWhisperEngine()
    else:
        raise ValueError(f"unknown engine: {name}")


# ============================================================================
# Clock Validation (compare virtual vs realtime on same clips)
# ============================================================================


async def validate_clocks(limit: int) -> None:
    """Run first `limit` clips both ways, compute CER on normalized hypotheses.

    Writes out/clock_validation.json with per-segment CER and pass/fail decision.
    """
    import soundfile

    segments = load_dataset()
    if limit:
        segments = segments[:limit]

    # Build engine once, reuse.
    engine = build_engine("turbo")  # arbitrary choice for validation
    engine.warmup()
    engine_proxy = TimingSTT(engine)

    from backend.vad import SileroVAD

    vad = SileroVAD()

    results_per_seg: list[dict[str, Any]] = []
    max_cer = 0.0

    for i, seg in enumerate(segments, 1):
        print(f"[{i}/{len(segments)}] clock validation: {seg.seg_id}...", end=" ", flush=True)

        # Virtual mode.
        clock = VirtualClock()
        with patched_clock(clock):
            rec_virtual = await process_clip(seg, engine_proxy, vad, clock, False)
        hyp_virtual = rec_virtual["hyp"]

        # Realtime mode.
        engine_proxy.reset()
        rec_realtime = await process_clip(seg, engine_proxy, vad, None, True)
        hyp_realtime = rec_realtime["hyp"]

        # CER on normalized.
        norm_virtual = normalize_ja(hyp_virtual)
        norm_realtime = normalize_ja(hyp_realtime)
        cer = _cer(norm_virtual, norm_realtime)
        max_cer = max(max_cer, cer)

        results_per_seg.append(
            {
                "seg_id": seg.seg_id,
                "cer": cer,
                "hyp_virtual": hyp_virtual,
                "hyp_realtime": hyp_realtime,
            }
        )
        print(f"cer={cer:.4f}")

    mean_cer = sum(r["cer"] for r in results_per_seg) / len(results_per_seg) if results_per_seg else 0.0
    passed = max_cer <= 0.01

    output = {
        "per_seg": results_per_seg,
        "max_cer": max_cer,
        "mean_cer": mean_cer,
        "pass": passed,
    }

    out_file = OUT_DIR / "clock_validation.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    print(f"written {out_file}")
    print(f"max_cer={max_cer:.4f}, pass={passed}")


def _cer(ref: str, hyp: str) -> float:
    """Character error rate via Levenshtein / max(len(ref), len(hyp), 1)."""
    if ref == hyp:
        return 0.0
    # Simple Levenshtein.
    m, n = len(ref), len(hyp)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[m][n] / max(m, n, 1)


# ============================================================================
# Main Pipeline Run
# ============================================================================


async def run_pipeline(engine_name: str, limit: int | None, realtime: bool) -> None:
    """Feed all clips through AudioSession, collect final events, write output.

    Resumes if output file exists (unless --limit or --compare-clocks was set).
    """
    segments = load_dataset()
    if limit:
        segments = segments[:limit]

    engine = build_engine(engine_name)
    engine.warmup()
    engine_proxy = TimingSTT(engine)

    from backend.vad import SileroVAD

    vad = SileroVAD()

    out_dir = OUT_DIR / engine_name
    out_dir.mkdir(parents=True, exist_ok=True)
    out_suffix = "_realtime" if realtime else ""
    out_file = out_dir / f"pipeline_transcripts{out_suffix}.jsonl"

    # Simple resume: skip seg_ids already in the output file.
    seen_seg_ids = set()
    if out_file.exists() and limit is None:
        for line in out_file.open(encoding="utf-8"):
            if line.strip():
                record = json.loads(line)
                seen_seg_ids.add(record["seg_id"])

    # Process each clip.
    clock = VirtualClock() if not realtime else None
    if not realtime and clock is not None:
        ctx = patched_clock(clock)
        ctx.__enter__()
    else:
        ctx = None

    # --limit is a testing knob: overwrite so a partial run never contaminates
    # a real full-run output file. Full runs append (resume-friendly).
    write_mode = "w" if limit is not None else ("a" if out_file.exists() else "w")
    try:
        with out_file.open(write_mode, encoding="utf-8") as out_f:
            for i, seg in enumerate(segments, 1):
                if seg.seg_id in seen_seg_ids:
                    continue

                print(
                    f"[{i}/{len(segments)}] {seg.seg_id}...",
                    end=" ",
                    flush=True,
                )
                engine_proxy.reset()

                record = await process_clip(seg, engine_proxy, vad, clock, realtime)
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()

                print(f"n_finals={record['n_finals']} :: {record['hyp'][:48]}")
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    print(f"written {out_file}")


# ============================================================================
# Self-Check (no GPU, no backend imports)
# ============================================================================


def check() -> None:
    """Self-check without GPU or backend imports. Print 'ok' and return 0 on success."""
    print("self-check: chunking...", end=" ", flush=True)
    # Test chunking round-trip for various lengths.
    for n in [1000, 4800, 4801, 10000, 100000]:
        audio = np.random.randn(n).astype(np.float32)
        chunks = []
        for start in range(0, len(audio), CHUNK_SAMPLES):
            chunks.append(audio[start : start + CHUNK_SAMPLES])
        reconstructed = np.concatenate(chunks)
        assert len(reconstructed) == n, f"chunking failed: {n} -> {len(reconstructed)}"
    print("ok")

    print("self-check: float32 <-> PCM16 round-trip...", end=" ", flush=True)
    for _ in range(10):
        orig = np.random.uniform(-1, 1, 1000).astype(np.float32)
        pcm16 = (np.clip(orig, -1, 1) * 32768.0).astype("<i2").tobytes()
        restored = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        max_err = np.max(np.abs(orig - restored))
        assert max_err < 2e-4, f"round-trip error: {max_err}"
    print("ok")

    print("self-check: assemble_record...", end=" ", flush=True)
    # Synthetic data.
    seg_mock = type("Segment", (), {
        "seg_id": "test_seg",
        "category": "게임",
        "duration_s": 5.0,
        "ja_ref": "こんにちは",
        "ko_ref": "안녕하세요",
    })()
    events = [
        {"type": "partial", "text": "こん"},
        {"type": "final", "text": "こんにちは"},
        {"type": "final", "text": ""},  # empty final
        {"type": "final", "text": "元気ですか"},
    ]
    timing_calls = [(True, 0.1), (False, 0.5), (False, 0.6)]
    reason_counts = {"hard_cap": 1, "silence_complete": 0}
    record = assemble_record(seg_mock, events, timing_calls, reason_counts)
    assert record["hyp"] == "こんにちは 元気ですか", f"hyp mismatch: {record['hyp']}"
    assert record["n_finals"] == 2, f"n_finals: {record['n_finals']}"
    assert record["n_final_events"] == 3, f"n_final_events: {record['n_final_events']}"
    assert record["n_dropped_finals"] == 1, f"n_dropped_finals: {record['n_dropped_finals']}"
    assert record["n_partial_calls"] == 1, f"n_partial_calls: {record['n_partial_calls']}"
    assert abs(record["stt_elapsed_s"] - 1.2) < 1e-9, f"stt_elapsed_s: {record['stt_elapsed_s']}"
    assert abs(record["stt_elapsed_s_partial"] - 0.1) < 1e-9, f"stt_elapsed_s_partial: {record['stt_elapsed_s_partial']}"
    assert abs(record["stt_elapsed_s_final"] - 1.1) < 1e-9, f"stt_elapsed_s_final: {record['stt_elapsed_s_final']}"
    assert record["finalize_reason_counts"]["hard_cap"] == 1
    assert record["finalize_reason_counts"]["grace_expired"] == 0
    print("ok")

    print("self-check: finalize_trigger regex...", end=" ", flush=True)
    re_trigger = re.compile(r"finalize trigger=(\w+)")
    test_logs = [
        "finalize trigger=hard_cap seg=xyz silence_ms=0",
        "finalize trigger=grace_expired seg=abc",
        "finalize trigger=silence_complete",
        "finalize trigger=strong_boundary",
    ]
    expected = ["hard_cap", "grace_expired", "silence_complete", "strong_boundary"]
    for log, exp in zip(test_logs, expected):
        m = re_trigger.search(log)
        assert m is not None, f"regex failed on: {log}"
        assert m.group(1) == exp, f"expected {exp}, got {m.group(1)}"
    print("ok")

    print("ok")


# ============================================================================
# CLI
# ============================================================================


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run real AudioSession pipeline on dataset clips."
    )
    parser.add_argument(
        "--engine",
        type=str,
        choices=["qwen3-asr-1.7b", "turbo"],
        default=None,
        help="STT engine to use",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Process first N clips only"
    )
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="Use real asyncio.sleep (not patched clock)",
    )
    parser.add_argument(
        "--compare-clocks",
        action="store_true",
        help="Run first N clips both ways, validate clock equivalence",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Self-check (no GPU, no backend imports)",
    )
    args = parser.parse_args()

    if args.check:
        check()
        return

    if args.compare_clocks:
        await validate_clocks(args.limit or 10)
        return

    if not args.engine:
        parser.error("--engine required (unless --check or --compare-clocks)")

    await run_pipeline(args.engine, args.limit, args.realtime)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
