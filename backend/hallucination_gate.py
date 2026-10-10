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
_ONNX_MAX_LENGTH = 128  # matches the ST model's max_seq_length


class _OnnxEmbedder:
    """Torch-free replacement for SentenceTransformer.encode, for the
    paraphrase-multilingual-MiniLM-L12-v2 ONNX export. Mirrors the ST
    pipeline: tokenize (truncate 128, pad) -> last_hidden_state -> attention-
    mask-weighted mean pooling -> L2 normalize. Only onnxruntime, tokenizers,
    huggingface_hub and numpy are imported here."""

    def __init__(self, repo: str) -> None:
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        model_path = hf_hub_download(repo, "onnx/model.onnx")
        tokenizer_path = hf_hub_download(repo, "tokenizer.json")
        tok = Tokenizer.from_file(tokenizer_path)
        pad_id = tok.token_to_id("<pad>")
        if pad_id is None:
            pad_id = 0
        tok.enable_truncation(max_length=_ONNX_MAX_LENGTH)
        tok.enable_padding(pad_id=pad_id, pad_token="<pad>")
        self._tok = tok
        self._sess = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._sess.get_inputs()}
        self._output_name = self._sess.get_outputs()[0].name

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> np.ndarray:
        encs = self._tok.encode_batch(texts)
        input_ids = np.array([e.ids for e in encs], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        feeds = {k: v for k, v in feeds.items() if k in self._input_names}
        hidden = self._sess.run([self._output_name], feeds)[0]
        mask = attention_mask[..., None].astype(np.float32)
        summed = (hidden * mask).sum(axis=1)
        pooled = summed / np.clip(mask.sum(axis=1), 1e-9, None)
        if normalize_embeddings:
            pooled = pooled / np.clip(
                np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None
            )
        return pooled.astype(np.float32)


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
        if config.HALLUCINATION_GATE_BACKEND == "onnx":
            self._model = _OnnxEmbedder(config.HALLUCINATION_GATE_ONNX_REPO)
            model_name = config.HALLUCINATION_GATE_ONNX_REPO
        else:
            # Imported lazily: sentence-transformers pulls in transformers/torch
            # machinery not otherwise needed if this gate is disabled (or if
            # the onnx backend is selected).
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(config.HALLUCINATION_GATE_MODEL)
            model_name = config.HALLUCINATION_GATE_MODEL
        self._ref_embeddings = self._model.encode(
            self._ref_phrases, normalize_embeddings=True
        )
        logger.info(
            "Hallucination gate ready (%d known phrases, backend=%s, model=%s)",
            len(self._ref_phrases),
            config.HALLUCINATION_GATE_BACKEND,
            model_name,
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
