"""Per-utterance speaker labeling via ECAPA-TDNN embeddings + online cosine
clustering — first cut at 2차 목표 (다중 화자), started 2026-08-19.

Deliberately NOT a full diarization pipeline (e.g. pyannote's
speaker-diarization, which bundles its own VAD + segmentation + clustering +
overlap detection). Those pipelines are built to run once over a whole,
already-recorded file — they need enough context to build stable speaker
embeddings and cluster them, which doesn't fit a 0.3s-chunk streaming
pipeline with a 1-2s latency target. Instead, this reuses the utterance
boundaries our own VAD/silence-based segmentation (audio_session.py) already
produces, and only answers "which speaker said this whole utterance?" once
per finalized sentence — diarization work never touches the per-frame hot
path.

Uses speechbrain's ECAPA-TDNN speaker-embedding model. Chosen over pyannote's
own embedding models specifically because pyannote's Hugging Face repos are
gated (require accepting terms via an HF account before they'll download) —
speechbrain/spkrec-ecapa-voxceleb is openly downloadable, avoiding that setup
friction. This is still the same swappable-engine shape as backend/stt and
backend/translation (see base.py); nothing outside this file assumes
speechbrain specifically, so it can be swapped for a pyannote-backed
implementation later if the gating is worth it for accuracy.

KNOWN LIMITATION: this labels non-overlapping, turn-taking speech only —
whichever speaker's utterance a whole VAD-bounded segment belongs to. It
cannot split a single utterance where two speakers talk over each other;
that needs true frame-level diarization (a candidate follow-up: a lightweight
embedding-drift check inline in the frame loop as a new finalize trigger,
discussed but not implemented as of 2026-08-19).

SPEAKER_ID_SIM_THRESHOLD has NOT been tuned against real labeled data yet —
unlike HALLUCINATION_GATE_SIM_THRESHOLD, which went through several rounds of
evidence-based adjustment (see config.py), this is a first guess. Expect to
revisit once real multi-speaker captures are labeled.
"""

from __future__ import annotations

import logging

import numpy as np

from backend import config

logger = logging.getLogger("live-translator.backend")


class SpeechbrainSpeakerIdentifier:
    def __init__(
        self,
        model_source: str = config.SPEAKER_ID_MODEL,
        sim_threshold: float = config.SPEAKER_ID_SIM_THRESHOLD,
        min_audio_s: float = config.SPEAKER_ID_MIN_AUDIO_S,
        sample_rate: int = config.SAMPLE_RATE,
    ) -> None:
        # Lazy import: speechbrain pulls in its own dependency stack, and
        # this whole module is 2차 목표 work — a backend without speechbrain
        # installed should still start and run 1차 목표 (single-speaker)
        # capture fine (see config.SPEAKER_ID_ENABLED).
        from speechbrain.inference.speaker import EncoderClassifier
        from speechbrain.utils.fetching import LocalStrategy

        self._classifier = EncoderClassifier.from_hparams(
            source=model_source,
            savedir=f"backend/models/{model_source.replace('/', '_')}",
            # Default SYMLINK strategy needs Windows Developer Mode/admin
            # rights (fails with WinError 1314 otherwise) — copy the files
            # instead. Slightly more disk use, no behavior difference.
            local_strategy=LocalStrategy.COPY,
        )
        self._sim_threshold = sim_threshold
        self._min_audio_s = min_audio_s
        self._sample_rate = sample_rate
        # (label, running-mean L2-normalized centroid, sample count) per
        # known speaker.
        self._centroids: list[tuple[str, np.ndarray, int]] = []
        self._next_id = 1

    def reset(self) -> None:
        # Per-session state, intentionally not persisted across captures —
        # "화자 1"/"화자 2" labels are only meant to stay stable within one
        # live capture, not identify a real person across sessions.
        self._centroids = []
        self._next_id = 1

    def identify(self, audio: np.ndarray, sample_rate: int) -> str | None:
        if audio.size == 0 or audio.size / sample_rate < self._min_audio_s:
            # Too short for a reliable embedding (e.g. a one-word utterance)
            # — leave unlabeled rather than guess.
            return None
        if sample_rate != self._sample_rate:
            logger.warning(
                "speaker_id: unexpected sample_rate=%d (expected %d) — skipping",
                sample_rate,
                self._sample_rate,
            )
            return None

        import torch

        try:
            with torch.no_grad():
                waveform = torch.from_numpy(audio).unsqueeze(0)
                embedding = self._classifier.encode_batch(waveform).squeeze().cpu().numpy()
        except Exception:
            logger.exception("speaker embedding extraction failed — leaving utterance unlabeled")
            return None
        embedding = embedding / (np.linalg.norm(embedding) + 1e-9)

        best_label, best_sim = None, -1.0
        for label, centroid, _count in self._centroids:
            sim = float(np.dot(embedding, centroid))
            if sim > best_sim:
                best_label, best_sim = label, sim

        if best_label is not None and best_sim >= self._sim_threshold:
            for i, (label, centroid, count) in enumerate(self._centroids):
                if label == best_label:
                    new_count = count + 1
                    new_centroid = (centroid * count + embedding) / new_count
                    new_centroid = new_centroid / (np.linalg.norm(new_centroid) + 1e-9)
                    self._centroids[i] = (label, new_centroid, new_count)
                    break
            return best_label

        new_label = f"화자 {self._next_id}"
        self._next_id += 1
        self._centroids.append((new_label, embedding, 1))
        return new_label
