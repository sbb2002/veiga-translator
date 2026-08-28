"""Virtual monotonic clock for feeding audio through the real AudioSession
pipeline faster than real time without breaking its wall-clock-dependent
finalize logic.

Why this exists: `backend/audio_session.py` drives several decisions off
`time.monotonic()` — `duration_s()` (hard cap, `MIN_PARTIAL_AUDIO_SECONDS`
partial gate), the `PARTIAL_UPDATE_INTERVAL_S` throttle, and the adaptive
VAD pause/rate EMAs. `silence_ms` is already audio-derived (accumulated
`frame_ms`), so the finalize *timing* on silence is clock-independent; but
if we feed a file as fast as compute allows, the wall clock barely advances
and the partial track (and therefore `looks_complete` /
`has_strong_sentence_boundary`, which read the partial transcript) never
fires the way it does in production.

Fix: advance a virtual clock by exactly the audio duration consumed, and
make AudioSession read that instead of the real clock.

Two patch points (see `patched_clock`):
  1. `backend.audio_session.time` -> a shim whose `.monotonic()` is the
     virtual clock. Covers every `time.monotonic()` call in method bodies.
     NOT a global `time.monotonic` patch — the asyncio event loop and the
     STT engine threads must keep the real clock.
  2. `AudioSession._process_frame` wrapper that snaps a freshly-created
     `_UtteranceState.started_at` to the virtual clock. Needed because
     `started_at = field(default_factory=time.monotonic)` binds the real
     function at class-creation time, so patch point 1 can't reach it.
     The snap lags creation by one frame (~32 ms) — immaterial, and the
     partial gate can't trip on frame 1 anyway (duration ~0).

Usage:
    clock = VirtualClock()
    with patched_clock(clock):
        session = AudioSession(...)
        for chunk in chunks:
            clock.advance(len(chunk) / SAMPLE_RATE)
            await session.feed_audio(chunk_bytes)
        await session.close()

The realtime path (validation harness) simply does NOT enter
`patched_clock` and uses real `asyncio.sleep` between chunks instead.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


class VirtualClock:
    """Monotonic, single-threaded. `advance` is called by the feed loop
    before each chunk; every AudioSession read in between sees the same
    value."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = float(start)

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError(f"advance by negative: {seconds}")
        self._t += float(seconds)

    @property
    def now(self) -> float:
        return self._t


class _TimeShim:
    """Stand-in for the `time` module as seen from inside
    `backend.audio_session`. Only `.monotonic` is overridden; anything else
    that module might reference falls through to the real module."""

    def __init__(self, clock: VirtualClock, real_time) -> None:
        self._clock = clock
        self._real = real_time

    def monotonic(self) -> float:
        return self._clock.monotonic()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


@contextmanager
def patched_clock(clock: VirtualClock) -> Iterator[None]:
    """Redirect AudioSession's clock reads to `clock` for the duration of
    the context, then fully restore. Import of `backend.audio_session`
    happens here so callers that only want `VirtualClock` don't pay it."""
    import time as _real_time

    from backend import audio_session as _as

    orig_time = _as.time
    orig_process_frame = _as.AudioSession._process_frame

    shim = _TimeShim(clock, _real_time)

    async def _process_frame_snapping(self, frame):  # type: ignore[no-untyped-def]
        prev = self._utterance
        await orig_process_frame(self, frame)
        cur = self._utterance
        if cur is not None and cur is not prev:
            # Fresh utterance: its started_at came from the real clock via
            # the dataclass default_factory. Snap it onto the virtual line.
            cur.started_at = clock.monotonic()

    _as.time = shim
    _as.AudioSession._process_frame = _process_frame_snapping
    try:
        yield
    finally:
        _as.time = orig_time
        _as.AudioSession._process_frame = orig_process_frame


if __name__ == "__main__":
    c = VirtualClock()
    assert c.monotonic() == 0.0
    c.advance(0.3)
    c.advance(0.3)
    assert abs(c.monotonic() - 0.6) < 1e-9, c.monotonic()
    try:
        c.advance(-1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on negative advance")

    try:
        from backend import audio_session as _as  # noqa: F401
    except Exception as e:  # pragma: no cover - depends on run location
        print(f"clock arithmetic ok; skipped patch test (no backend import: {e})")
    else:
        real_time_mod = _as.time
        real_pf = _as.AudioSession._process_frame
        with patched_clock(c):
            assert _as.time is not real_time_mod
            assert _as.time.monotonic() == c.monotonic()
            assert _as.AudioSession._process_frame is not real_pf
        assert _as.time is real_time_mod, "time not restored"
        assert _as.AudioSession._process_frame is real_pf, "_process_frame not restored"
        print("clock + patch/restore ok")
