"""Lightweight speech/music discriminator that gates SileroVAD (vad.py).

Un-deferred 2026-08-19 (CLAUDE.md previously pushed all "song/music
environment" work to phase 3): live capture showed background music playing
under normal speech drives residual outro-phrase hallucinations even after
the no_speech_prob/avg_logprob and audio-RMS gates (audio_session.py) —
BGM keeps the raw RMS elevated during stretches where nobody is actually
talking, and silero-VAD itself can mistake rhythmic music for speech.

Real vocal separation (e.g. HTDemucs) was considered and rejected here: it's
built for offline/batch processing of multi-second windows, and a real-time
streaming version fights this project's 1-2s end-to-end latency target
while adding GPU load that competes with STT/translation. This module is
pure signal processing instead — no model weights, negligible CPU cost.

Feature: the classic Scheirer/Slaney "4Hz modulation energy" heuristic.
Speech's short-time energy envelope has a pronounced peak around 3-6Hz from
syllable rate; most music (rhythmic at a different, usually slower, beat
rate, or just harmonically sustained) doesn't. Track a rolling energy
envelope across incoming VAD-sized frames and, once there's enough context,
say "no, this stretch doesn't look like speech" when the syllable band shows
no real peak — used to override VAD's own "is_speech" rather than replace it
outright, since this coarse-window heuristic is far less reliable than VAD's
frame-level model on its own.

Validated with synthetic AM-noise signals (band-limited noise carrier with/
without a 3-6Hz envelope), not real captured audio — see the module test
snippets referenced from the 2026-08-19 conversation history. One known weak
spot found during that testing: a handful of *pure, near-integer-cycles-per-
frame sustained tones* produced a spurious envelope peak from frame-boundary
phase aliasing, landing in-band by coincidence. Real music (percussion,
noise-like texture, non-stationary spectra) is very unlikely to hit this
exact degenerate case, but it means this heuristic isn't provably airtight
against synthetic/electronic music built from very clean sustained tones.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from backend import config


class MusicGate:
    def __init__(
        self,
        frame_seconds: float = config.VAD_FRAME_SAMPLES / config.SAMPLE_RATE,
        window_seconds: float = config.MUSIC_GATE_WINDOW_S,
        ratio_threshold: float = config.MUSIC_GATE_MODULATION_RATIO_THRESHOLD,
    ) -> None:
        self._frame_seconds = frame_seconds
        self._ratio_threshold = ratio_threshold
        window_samples = max(8, round(window_seconds / frame_seconds))
        self._envelope: deque[float] = deque(maxlen=window_samples)
        self._min_samples = window_samples  # wait for a full window before judging

    def reset(self) -> None:
        self._envelope.clear()

    def push(self, frame: np.ndarray) -> None:
        self._envelope.append(float(np.sqrt(np.mean(np.square(frame)))))

    def is_music_dominant(self) -> bool:
        """VAD-gating entry point — respects config.MUSIC_GATE_ENABLED (off
        by default, see its comment: this heuristic once dropped genuine
        speech live). True only when there's positive evidence this stretch
        is music, not speech — defaults to False (trust VAD) whenever there
        isn't enough context yet or the signal is too quiet to judge,
        matching this codebase's general bias toward not dropping real
        speech on an ambiguous read (see config.py's threshold comments)."""
        if not config.MUSIC_GATE_ENABLED:
            return False
        return self._compute_music_dominant()

    def music_suspected(self) -> bool:
        """Same modulation-spectrum read as is_music_dominant(), but ALWAYS
        active regardless of config.MUSIC_GATE_ENABLED (2026-08-25) — for
        display-only use (e.g. tagging a finalized segment so the UI can
        show a "🎵 music" placeholder instead of a confidently-wrong
        translation). Safe to bypass the flag here specifically because
        nothing downstream of this call can suppress capture or drop real
        speech — worst case is a wrong placeholder shown over genuine short
        speech, not lost audio."""
        return self._compute_music_dominant()

    def _compute_music_dominant(self) -> bool:
        if len(self._envelope) < self._min_samples:
            return False
        env = np.array(self._envelope, dtype=np.float32)
        if env.max() < config.AUDIO_RMS_SILENCE_FLOOR:
            return False  # near-silent window — the RMS gate already handles this case
        env = env - env.mean()
        spectrum = np.abs(np.fft.rfft(env * np.hanning(len(env))))
        freqs = np.fft.rfftfreq(len(env), d=self._frame_seconds)
        ac_spectrum = spectrum[1:]  # exclude DC
        if ac_spectrum.sum() <= 1e-9:
            # Loud (already passed the silence check above) but an
            # essentially flat energy envelope — no modulation at all is
            # itself strong evidence against speech, not an ambiguous case
            # to fall back on VAD for (real speech, even a flat monotone
            # reading, still varies syllable-to-syllable).
            return True
        # Peak PROMINENCE in the syllable band, not raw energy sum: a chord
        # (multiple simultaneous sustained notes) beats against itself and
        # can spread real energy across the same 2-8Hz range from harmonic
        # interference alone, with no single dominant frequency — a sum-based
        # ratio false-negatives on that. Speech's syllable rate instead shows
        # up as a comparatively sharp peak. Require the tallest bin in a
        # narrower 3-5.5Hz core band to clear the *typical* (median) bin
        # elsewhere by a solid margin before trusting it as a real peak.
        band_mask = (freqs >= 3.0) & (freqs <= 5.5)
        if not band_mask.any():
            return False  # window too short for this resolution — don't guess
        peak = spectrum[band_mask].max()
        background = np.median(ac_spectrum)
        prominence = peak / (background + 1e-9)
        # bool(...): numpy comparisons return numpy.bool_, not a plain
        # Python bool — json.dumps() rejects numpy.bool_ outright. That bug
        # silently broke every "final" event's websocket send once this
        # method actually reached this line (SessionLogger.log_event's
        # json.dumps ran before send_text, so the whole event — not just
        # the log write — was dropped; see main.py's send_event ordering).
        return bool(prominence < self._ratio_threshold)
