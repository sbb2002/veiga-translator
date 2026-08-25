"""Per-utterance singing detector.

Redesigned 2026-08-25 (user direction — full purpose change from the
original "catch background music playing under speech" idea): detect
whether the SPEAKER THEMSELVES is singing, not whether music is present in
general.

Two prior approaches tried and replaced within the same day:
1. Rolling 2s energy-envelope window, FFT for a 3-5.5Hz syllable-rate peak,
   gating VAD. Dropped: sung lyrics carry their own syllable-rate energy
   modulation similar to speech, so "no syllable peak = music" routinely
   missed real singing; it also read a session-wide rolling window
   asynchronously, sometimes after the utterance it was meant to describe
   had already ended, misattributing the judgment.
2. Pitch (F0) tracking directly on the utterance's raw captured audio
   (autocorrelation), synchronous at finalize time — fixed the timing bug,
   worked well for a cappella singing, but a loud background instrumental
   mixed into the same raw audio corrupted the pitch read: autocorrelation
   would lock onto whichever source (voice or accompaniment) was more
   tonally dominant frame-to-frame, producing an erratic/wide pitch track
   for perfectly normal talking-over-BGM and false-positiving as singing.

This version adds a Demucs (htdemucs) vocal-separation pass before pitch
tracking — isolates the speaker's voice from any underlying music first, so
the pitch read reflects only what the speaker themselves is doing. This is
what docs/planning/IMPROVEMENT_BACKLOG.md's M1 item was investigating for a
later phase; moved up to now on user request once a live capture showed the
BGM-contamination failure mode directly. Per-utterance latency cost was
initially measured at ~2.4s (misleading — that included one-time CUDA
kernel compilation); warmed up via MusicGate.warmup() at startup, subsequent
separations run ~100-200ms regardless of clip length, well within budget.

Pitch stats are still estimated with the same pure-numpy autocorrelation
approach as before, just on the separated vocal track instead of the raw
mix. AudioSession compares each utterance's stats against a session-adaptive
baseline of that speaker's own (separated) conversational voice
(config.ADAPTIVE_SINGING_*) rather than a fixed one-size-fits-all number.
"""

from __future__ import annotations

import numpy as np
import torch
import torchaudio

from backend import config


def _median_filter(arr: np.ndarray, window: int) -> np.ndarray:
    """Simple centered median filter, odd window only (rounded up if even).
    No scipy dependency — this project doesn't otherwise need it, and a
    short 1D median filter is easy enough to do directly."""
    if window < 3 or len(arr) < window:
        return arr
    if window % 2 == 0:
        window += 1
    half = window // 2
    padded = np.pad(arr, (half, half), mode="edge")
    return np.array(
        [np.median(padded[i : i + window]) for i in range(len(arr))],
        dtype=arr.dtype,
    )


class MusicGate:
    def __init__(self, sample_rate: int = config.SAMPLE_RATE, device: str | None = None) -> None:
        self._sample_rate = sample_rate
        self._frame_len = int(sample_rate * config.PITCH_FRAME_MS / 1000)
        self._hop_len = int(sample_rate * config.PITCH_HOP_MS / 1000)
        self._min_lag = max(1, int(sample_rate / config.PITCH_MAX_HZ))
        self._max_lag = int(sample_rate / config.PITCH_MIN_HZ)
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._separator = None
        self._resample_to_separator: torchaudio.transforms.Resample | None = None
        self._resample_from_separator: torchaudio.transforms.Resample | None = None

    def warmup(self) -> None:
        """DEPRECATED 2026-08-26 (user call): Demucs vocal separation loading
        disabled. It was working — live capture showed it correctly
        distinguishing background music from a speaker's own singing, and
        catching genuine singing after the median-filter fix — but its
        get_model() HF Hub freshness check hung on a flaky network mid-
        session, blocking the whole app's startup (FastAPI doesn't finish
        `startup()`, so no websocket connections are accepted at all) and
        killing live transcription/translation with no separation-specific
        symptom to point at. Reverting pitch_stats() to operate on raw
        (non-separated) audio — see that method's `if self._separator is
        not None` gate below, which now always takes the raw-audio branch
        since self._separator stays None. Restore by uncommenting the block
        below; if the network-hang risk needs fixing first, look at forcing
        HF_HUB_OFFLINE=1 once the model is confirmed cached, or vendoring
        the weights so get_model() never touches the network.
        # from demucs.pretrained import get_model
        #
        # self._separator = get_model(config.DEMUCS_MODEL_NAME)
        # self._separator.eval()
        # self._separator.to(self._device)
        # separator_rate = self._separator.samplerate
        # self._resample_to_separator = torchaudio.transforms.Resample(
        #     orig_freq=self._sample_rate, new_freq=separator_rate
        # ).to(self._device)
        # self._resample_from_separator = torchaudio.transforms.Resample(
        #     orig_freq=separator_rate, new_freq=self._sample_rate
        # ).to(self._device)
        # self._isolate_vocals(np.zeros(self._sample_rate, dtype=np.float32))
        """
        return

    def pitch_stats(self, audio: np.ndarray) -> tuple[float, float] | None:
        """Median F0 (Hz) and pitch range (semitones, robust 10th-90th
        percentile spread) across voiced frames of `audio`'s isolated vocal
        track, or None when there aren't enough voiced frames to judge
        (silence, no vocal content, or too short an utterance). Call once
        per utterance, on its full buffered audio, right when it finalizes.
        `audio` must be mono float32 at self._sample_rate."""
        vocals = self._isolate_vocals(audio) if self._separator is not None else audio
        f0s = self._estimate_f0_track(vocals)
        if len(f0s) < config.PITCH_MIN_VOICED_FRAMES:
            return None
        # Median-filter the frame-level track before computing stats — live
        # capture (2026-08-26) showed plain conversational speech coming
        # back with 10-18 semitones of "range", enough to swamp any sane
        # singing threshold. Root cause: autocorrelation pitch tracking is
        # prone to octave errors (locking onto a harmonic/sub-harmonic for
        # an isolated frame or two), and a handful of octave-doubled/halved
        # outlier frames blow up a percentile-based range even though the
        # speaker's actual pitch barely moved. A short median filter is the
        # standard fix — it kills isolated single-frame octave jumps while
        # leaving genuine sustained pitch movement (real singing) intact.
        arr = _median_filter(np.array(f0s, dtype=np.float64), config.PITCH_MEDIAN_FILTER_FRAMES)
        median_f0 = float(np.median(arr))
        lo, hi = np.percentile(arr, [10, 90])
        lo = max(lo, 1e-6)
        hi = max(hi, lo)
        range_semitones = float(12.0 * np.log2(hi / lo))
        return median_f0, range_semitones

    def _isolate_vocals(self, audio: np.ndarray) -> np.ndarray:
        """Demucs vocal separation — pulls the speaker's voice out from
        underneath any background music/BGM before pitch analysis, so a
        loud instrumental track underneath normal talking no longer
        corrupts the pitch read. `audio`: mono float32 at
        self._sample_rate. Returns mono float32 vocals-only audio at the
        same sample rate."""
        from demucs.apply import apply_model

        if audio.size == 0:
            return audio
        with torch.no_grad():
            wav = torch.from_numpy(audio).to(self._device).unsqueeze(0)  # [1, T]
            wav = self._resample_to_separator(wav)
            stereo = wav.repeat(2, 1)  # demucs expects stereo input
            # Demucs expects roughly zero-mean/unit-std input and rescales
            # its own output back afterward — see demucs.separate's
            # reference usage. Skipping this (tried first) silently produced
            # near-silent "vocals" output for perfectly normal speech, since
            # our audio's actual amplitude range doesn't match what the
            # model was trained to expect.
            ref_mean = stereo.mean()
            ref_std = stereo.std().clamp_min(1e-8)
            normalized = (stereo - ref_mean) / ref_std
            sources = apply_model(
                self._separator, normalized.unsqueeze(0), device=self._device, progress=False
            )[0]  # [n_sources, 2, T]
            sources = sources * ref_std + ref_mean
            vocals_idx = self._separator.sources.index("vocals")
            vocals_mono = sources[vocals_idx].mean(dim=0, keepdim=True)  # [1, T]
            vocals_back = self._resample_from_separator(vocals_mono)
            return vocals_back.squeeze(0).cpu().numpy().astype(np.float32)

    def _estimate_f0_track(self, audio: np.ndarray) -> list[float]:
        """Per-frame F0 via normalized autocorrelation. Frames that are too
        quiet (silence/near-silence) or whose autocorrelation has no clear
        periodicity peak (unvoiced consonants, separation artifacts) are
        skipped rather than forced into the track — only genuinely voiced
        frames should influence pitch_stats."""
        if len(audio) < self._frame_len or self._max_lag >= self._frame_len:
            return []
        f0s: list[float] = []
        for start in range(0, len(audio) - self._frame_len + 1, self._hop_len):
            frame = audio[start : start + self._frame_len]
            rms = float(np.sqrt(np.mean(np.square(frame))))
            if rms < config.PITCH_VOICED_ENERGY_FLOOR:
                continue
            centered = frame - frame.mean()
            corr = np.correlate(centered, centered, mode="full")[len(centered) - 1 :]
            zero_lag = corr[0]
            if zero_lag <= 0:
                continue
            segment = corr[self._min_lag : self._max_lag]
            if segment.size == 0:
                continue
            peak_idx = int(np.argmax(segment))
            peak_val = segment[peak_idx]
            if peak_val / zero_lag < config.PITCH_VOICING_THRESHOLD:
                continue  # no clear periodicity — not a confidently-voiced frame
            lag = self._min_lag + peak_idx
            f0s.append(self._sample_rate / lag)
        return f0s
