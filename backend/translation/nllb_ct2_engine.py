"""NLLB-200 (CTranslate2, CPU int8) translation engine.

Drop-in alternative to LlamaServerEngine for hardware profiles where an LLM is
too slow (profile p1nmt). A dedicated NMT model translates one sentence at a
time with no prompt, so it ignores context/glossary/topic hints. The
LLM-only extras (context summary, topic-change check) are disabled.

Engine selected by config.TRANSLATION_ENGINE == "nllb-ct2".
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import ctranslate2
import sentencepiece as spm

from backend import config
from backend.translation.base import TranslationResult

logger = logging.getLogger("live-translator.backend")

_SRC = "jpn_Jpan"
_TGT = "kor_Hang"


class NllbCt2Engine:
    def __init__(self) -> None:
        model_dir = Path(config.NLLB_MODEL_DIR)
        if not model_dir.is_absolute():
            model_dir = Path(__file__).resolve().parents[2] / model_dir
        self._sp = spm.SentencePieceProcessor(model_file=str(model_dir / "sentencepiece.bpe.model"))
        self._tr = ctranslate2.Translator(
            str(model_dir),
            device="cpu",
            compute_type=config.NLLB_COMPUTE_TYPE,
            inter_threads=1,
            intra_threads=config.NLLB_THREADS,
        )
        logger.info("NLLB CT2 engine loaded (%s, %s, threads=%d)", model_dir.name,
                    config.NLLB_COMPUTE_TYPE, config.NLLB_THREADS)

    def _translate_sync(self, text: str, src: str, tgt: str, beam: int) -> str:
        tokens = [src] + self._sp.encode(text, out_type=str) + ["</s>"]
        res = self._tr.translate_batch(
            [tokens],
            target_prefix=[[tgt]],
            beam_size=beam,
            max_decoding_length=config.NLLB_MAX_DECODING_LENGTH,
            no_repeat_ngram_size=config.NLLB_NO_REPEAT_NGRAM,
        )
        out = res[0].hypotheses[0]
        if out and out[0] == tgt:
            out = out[1:]
        return self._sp.decode(out).strip()

    async def verify_contract(self) -> None:
        # In-process model: nothing to probe. Warm up so the first real call is not cold.
        await asyncio.to_thread(self._translate_sync, "テスト", _SRC, _TGT, 1)
        logger.info("NLLB CT2 engine warm.")

    async def translate(self, text: str, *, fast: bool = False, **_ignored) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="")
        beam = config.NLLB_BEAM_FAST if fast else config.NLLB_BEAM_FINAL
        out = await asyncio.to_thread(self._translate_sync, text, _SRC, _TGT, beam)
        return TranslationResult(text=out)

    async def translate_ko_to_ja(self, text: str, *, context: str | None = None) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="")
        out = await asyncio.to_thread(self._translate_sync, text, _TGT, _SRC, config.NLLB_BEAM_FINAL)
        return TranslationResult(text=out)

    async def translate_title(self, text: str) -> TranslationResult:
        return await self.translate(text)

    async def summarize_context(self, ja_history: str) -> str:
        return ""  # LLM-only feature; disabled for NMT profiles

    async def context_changed(self, current_summary: str, recent_ja: str) -> bool:
        return False

    async def aclose(self) -> None:
        return None
