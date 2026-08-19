"""Embedding-similarity "Bag of Hallucinations" gate — added 2026-08-19.

Complements the no_speech_prob/avg_logprob gate in stt/faster_whisper_engine.py
rather than replacing it. Live-capture labeling (data/flagged_segments.jsonl)
proved those two Whisper confidence signals genuinely overlap between real
short Japanese utterances and one specific recurring hallucination family
(YouTube outro thank-yous, e.g. "ご視聴ありがとうございました") — no
probability threshold, however tuned, separates them (see conversation
history 2026-08-19 for the side-by-side ranges). Research on this exact
failure mode (Baranski et al. 2025, "Investigation of Whisper ASR
Hallucinations Induced by Non-Speech Audio") reaches the same conclusion and
proposes a "Bag of Hallucinations" (BoH) — a maintained list of known
recurring hallucinated phrases — as the standard complementary mitigation.

A literal string/regex BoH was tried and explicitly rejected earlier the
same day (whack-a-mole: a new wording variant needs its own pattern, and
never generalizes to the next one). This is a BoH matched by *meaning*
instead: embed both the known phrases (backend/known_hallucinations.json)
and the candidate STT segment with a multilingual sentence-embedding model,
and flag a match on cosine similarity — morphological variants of a known
phrase ("視聴して" vs "ご視聴", a space before ありがとう, 本当に inserted,
した vs ます) score similarly without needing their own entry.

Model: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, already
cached locally from another project (no extra download). Threshold 0.78
picked from real data: known phrase variants score >=0.91 against each
other; the closest real flagged utterance ("ごまそーいただきまーす",
sharing the token いただき) scored 0.69 — 0.78 sits in the gap with margin
on both sides. Revisit against a larger flagged_segments.jsonl sample.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from backend import config

logger = logging.getLogger("live-translator.backend")

_PHRASES_PATH = Path(__file__).parent / "known_hallucinations.json"


class HallucinationGate:
    def __init__(self, phrases_path: Path = _PHRASES_PATH) -> None:
        self._enabled = config.HALLUCINATION_GATE_ENABLED
        self._threshold = config.HALLUCINATION_GATE_SIM_THRESHOLD
        self._model = None
        self._ref_embeddings: np.ndarray | None = None
        self._ref_phrases: list[str] = []
        if not self._enabled:
            return
        data = json.loads(phrases_path.read_text(encoding="utf-8"))
        self._ref_phrases = data["phrases"]
        if not self._ref_phrases:
            self._enabled = False
            return
        # Imported lazily: sentence-transformers pulls in transformers/torch
        # machinery not otherwise needed if this gate is disabled.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(config.HALLUCINATION_GATE_MODEL)
        self._ref_embeddings = self._model.encode(
            self._ref_phrases, normalize_embeddings=True
        )
        logger.info(
            "Hallucination gate ready (%d known phrases, model=%s)",
            len(self._ref_phrases),
            config.HALLUCINATION_GATE_MODEL,
        )

    def is_known_hallucination(self, text: str) -> str | None:
        """Returns the matched known phrase (for logging) if `text` is
        semantically close enough to one, else None. Cheap early-outs so
        callers can check unconditionally without worrying about cost on
        the hot path."""
        if not self._enabled or not text.strip() or self._model is None:
            return None
        vec = self._model.encode([text], normalize_embeddings=True)[0]
        sims = self._ref_embeddings @ vec
        best_idx = int(np.argmax(sims))
        if sims[best_idx] >= self._threshold:
            return self._ref_phrases[best_idx]
        return None
