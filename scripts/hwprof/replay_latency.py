"""L3 end-to-end latency: replay the 150 eval clips to the live backend WebSocket
at wall-clock speed (0.3s frames, 1.0s silence between clips) and record when
each partial/final event arrives.

d_final = t_recv(final with text) - t_clip_end. Finals are matched to clips by a
monotonic alignment on normalized Japanese text (difflib), so unmatched/empty
finals are reported separately instead of silently skewing the numbers.
Drift slope = linear regression of d_final against clip end time (s per min);
> 0 means the pipeline falls further behind real time as the session runs.

Run from repo root with the backend already up:
    python scripts/hwprof/replay_latency.py --out research/topic/<dir>/out
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import websockets
from scipy.signal import resample_poly

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "research" / "topic" / "20260826_stt_model_survey_gpu_full" / "src"))
from common import load_dataset, normalize_ja  # noqa: E402

SR = 16000
FRAME = 4800  # 0.3s


def load_pcm(path) -> np.ndarray:
    a, sr = sf.read(str(path), dtype="float32")
    if a.ndim > 1:
        a = a.mean(axis=1)
    if sr != SR:
        a = resample_poly(a, SR, sr)
    return np.ascontiguousarray(a, dtype=np.float32)


def to_frames(audio: np.ndarray):
    for i in range(0, len(audio), FRAME):
        chunk = audio[i : i + FRAME]
        yield (np.clip(chunk, -1, 1) * 32768.0).astype("<i2").tobytes()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ws/audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--start", type=int, default=0, help="first clip index (smoke tests only)")
    ap.add_argument("--gap-s", type=float, default=1.0)
    ap.add_argument("--quiet-s", type=float, default=90.0, help="stop after this long with no events")
    ap.add_argument("--max-drain-s", type=float, default=1800.0)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    segs = load_dataset()
    segs = segs[args.start :]
    if args.limit:
        segs = segs[: args.limit]
    gap = np.zeros(int(args.gap_s * SR), dtype=np.float32)

    events: list[dict] = []
    clips: list[dict] = []
    live = open(out / "l3_events_live.jsonl", "w", encoding="utf-8")
    t0 = time.monotonic()
    last_event = [time.monotonic()]

    async with websockets.connect(args.url, max_size=None, ping_interval=None) as ws:
        await ws.send(json.dumps({"type": "start_session", "video_id": "l3-replay", "video_title": "l3-replay"}))

        async def receiver():
            async for msg in ws:
                now = time.monotonic() - t0
                ev = json.loads(msg)
                ev["t_recv"] = now
                events.append(ev)
                last_event[0] = time.monotonic()
                live.write(json.dumps(ev, ensure_ascii=False) + chr(10))
                live.flush()

        rtask = asyncio.create_task(receiver())

        async def stream():
            next_t = time.monotonic()
            for seg in segs:
                audio = load_pcm(seg.wav_path)
                clip = {"seg_id": seg.seg_id, "ja_ref": seg.ja_ref, "ko_ref": seg.ko_ref, "t_start": time.monotonic() - t0}
                for fr in list(to_frames(audio)) :
                    next_t += len(fr) / 2 / SR
                    await ws.send(fr)
                    await asyncio.sleep(max(0.0, next_t - time.monotonic()))
                clip["t_end"] = time.monotonic() - t0
                clips.append(clip)
                for fr in to_frames(gap):
                    next_t += len(fr) / 2 / SR
                    await ws.send(fr)
                    await asyncio.sleep(max(0.0, next_t - time.monotonic()))

        await stream()
        t_stream_end = time.monotonic() - t0
        # drain: wait until the backend has been quiet for --quiet-s
        deadline = time.monotonic() + args.max_drain_s
        while time.monotonic() < deadline and time.monotonic() - last_event[0] < args.quiet_s:
            await asyncio.sleep(2)
        drained_quiet = time.monotonic() - last_event[0] >= args.quiet_s
        try:
            await ws.send(json.dumps({"type": "stop_session"}))
        except Exception:
            pass
        rtask.cancel()

    with open(out / "l3_events.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    with open(out / "l3_clips.json", "w", encoding="utf-8") as f:
        json.dump(clips, f, ensure_ascii=False)

    # --- match finals to clips (monotonic alignment) ---
    finals = [e for e in events if e.get("type") == "final"]
    nonempty = [e for e in finals if (e.get("text") or "").strip()]
    cn = [normalize_ja(c["ja_ref"]) for c in clips]
    matched, unmatched, ci = [], [], 0
    for e in nonempty:
        t = normalize_ja(e["text"])
        best, bi = 0.0, None
        for j in range(ci, min(len(clips), ci + 4)):
            r = difflib.SequenceMatcher(None, t, cn[j]).ratio()
            if r > best:
                best, bi = r, j
        # a final can't precede its clip's start
        if bi is not None and best >= 0.25 and e["t_recv"] >= clips[bi]["t_start"]:
            matched.append((bi, e, best))
            ci = bi
        else:
            unmatched.append(e)
    # one value per clip: the last matched final of that clip (carries the full translation)
    per_clip: dict[int, dict] = {}
    for bi, e, r in matched:
        per_clip[bi] = {"clip": bi, "seg_id": clips[bi]["seg_id"], "t_end": clips[bi]["t_end"],
                        "t_recv": e["t_recv"], "d_final": e["t_recv"] - clips[bi]["t_end"],
                        "has_translation": bool((e.get("translation") or "").strip()), "sim": r}
    d = np.array([v["d_final"] for v in per_clip.values()])
    te = np.array([v["t_end"] for v in per_clip.values()])
    slope = float(np.polyfit(te / 60.0, d, 1)[0]) if len(d) > 2 else None
    first_partial = []
    for i, c in enumerate(clips):
        ps = [e["t_recv"] for e in events if e.get("type") == "partial" and c["t_start"] <= e["t_recv"] <= c["t_end"] + 5]
        if ps:
            first_partial.append(min(ps) - c["t_start"])
    summ = {
        "n_clips": len(clips), "n_events": len(events), "n_finals": len(finals), "n_finals_nonempty": len(nonempty),
        "n_clips_with_matched_final": len(per_clip), "n_unmatched_finals": len(unmatched),
        "stream_s": t_stream_end, "total_s": time.monotonic() - t0, "drained_quiet": drained_quiet,
        "d_final_p50": float(np.percentile(d, 50)) if len(d) else None,
        "d_final_p95": float(np.percentile(d, 95)) if len(d) else None,
        "d_final_max": float(d.max()) if len(d) else None,
        "drift_slope_s_per_min": slope,
        "first_partial_p50": float(np.percentile(first_partial, 50)) if first_partial else None,
        "n_final_without_translation": sum(1 for v in per_clip.values() if not v["has_translation"]),
    }
    with open(out / "l3_per_clip.jsonl", "w", encoding="utf-8") as f:
        for v in per_clip.values():
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    with open(out / "l3_summary.json", "w", encoding="utf-8") as f:
        json.dump(summ, f, ensure_ascii=False, indent=2)
    print(json.dumps(summ, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
