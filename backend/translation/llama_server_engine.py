"""llama-server (OpenAI-compatible /v1/chat/completions) translation engine.

Chosen over Ollama: llama-server is the same llama.cpp engine Ollama wraps,
without Ollama's ~10-30% single-stream overhead (see docs/PRD.md
benchmarking notes for sources). Swappable behind TranslationEngine —
dropping in vLLM/ExLlamaV2/etc later means writing a new class here, no
changes to audio_session.py.
"""

from __future__ import annotations

import httpx

from backend import config
from backend.translation.base import TranslationResult

_SLANG_NOTE = (
    "The Japanese input may contain casual internet/streamer slang, clipped "
    "abbreviations (e.g. ホラゲー = ホラー"
    "ゲーム = 'horror game'), and effort/exertion interjections or "
    "onomatopoeia (e.g. よいしょ, どっこいしょ — filler grunts said while "
    "doing something, natural in Korean as things like '영차' or '하나둘'). "
    "Infer the intended real meaning from context and translate it naturally "
    "into the closest natural Korean word or interjection — do not "
    "transliterate literally, and never invent a nonsense word."
)

_FILLER_NOTE = (
    "Hesitation fillers like えっと, あの, あのー, その, まあ have no fixed "
    "meaning of their own — render them as a short natural Korean filler "
    "(음..., 어..., 그... ) or simply drop them if the sentence reads better "
    "without one. Never invent an unrelated Korean word to fill an "
    "untranslatable filler."
)

_HONORIFIC_NOTE = (
    "Japanese name suffixes -ちゃん/-くん are affectionate nicknames between "
    "friends/fans, not formal address — render them as the Korean equivalent "
    "diminutive suffix '-짱'/'-군' attached to the (transliterated) name "
    "itself (e.g. リッちゃん -> 릿짱), never as formal '씨', and never "
    "substitute an unrelated Western name that happens to sound similar."
)

_FAST_SYSTEM_PROMPT = (
    "Translate the following Japanese text fragment into Korean. The fragment "
    "may be an incomplete sentence that is still being spoken. Translate every "
    "word into Korean, even if the fragment is ambiguous — use your best-guess "
    "Korean rendering (a Korean loanword approximation is fine) rather than "
    "leaving anything untranslated. " + _SLANG_NOTE + " " + _FILLER_NOTE + " "
    + _HONORIFIC_NOTE + " Output ONLY the Korean translation, nothing else — "
    "no notes, no romanization, no quotes."
)

_FINAL_SYSTEM_PROMPT = (
    "You are a professional Japanese-to-Korean translator. Translate the "
    "complete Japanese sentence under the '[TEXT TO TRANSLATE]' heading into "
    "natural, fluent, idiomatic Korean, as it would be spoken or subtitled. "
    "The user message may also include a '[PREVIOUS SENTENCE]' section — that "
    "is background context from the sentence spoken just before this one, "
    "given only to help you resolve ambiguity or continuity. Do NOT translate "
    "it and do NOT include it in your output; translate ONLY the text under "
    "'[TEXT TO TRANSLATE]'. " + _SLANG_NOTE + " " + _FILLER_NOTE + " "
    + _HONORIFIC_NOTE + " Output ONLY the Korean translation of that text, "
    "nothing else — no notes, no romanization, no quotes."
)

# Grammar-constrained decoding: restrict the output alphabet to an explicit
# ALLOW-list (Hangul + ASCII + general punctuation) rather than a blacklist
# of "known bad" scripts. A blacklist is whack-a-mole — after blocking CJK
# script leakage, manual testing turned up Cyrillic leaking through the same
# way (nothing was excluding it). A whitelist closes off every script we
# didn't explicitly allow, so no further script-specific patches should be
# needed. This guarantees script correctness only, not translation accuracy.
#
# GBNF only supports \xHH (single-byte) escapes, not \x{...}/\u{...}; ranges
# above U+00FF must appear as literal UTF-8 characters in the grammar text.
# Built from raw code points via chr() rather than pasted/typed glyphs — a
# pasted character can silently be a canonically-equivalent but different
# code point, which would silently corrupt a range boundary.
#
# Note this is deliberately NOT the full ASCII printable range. Allowing the
# full range (including [ ] / : \ | etc.) let the model, when its preferred
# masked token wasn't available, fall back to stray formatting-looking debris
# instead of a clean sentence (observed in manual testing: "...타자입니다"
# became "...:last 타자입니다", "받을지도!" became "받을지도!]", etc.). Cutting
# the allowed punctuation down to what a Korean sentence actually needs
# closes off that specific escape route.
_ALLOWED_SCRIPT_RANGES = [
    (0x0030, 0x0039),  # digits
    (0x0041, 0x005A),  # A-Z
    (0x0061, 0x007A),  # a-z
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0xAC00, 0xD7A3),  # Hangul Syllables
    (0x2000, 0x206F),  # General Punctuation (curly quotes, dashes, ellipsis, etc.)
]
# Space + a conservative punctuation set actually needed in a Korean
# sentence. '-' MUST stay last in the character class (unescaped '-'
# anywhere else would be read as a range operator).
_ALLOWED_SINGLE_CHARS = " .,!?~'\"()-"


def _build_korean_only_grammar() -> str:
    ranges = "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _ALLOWED_SCRIPT_RANGES)
    return f"root ::= safe-char+\nsafe-char ::= [{ranges}{_ALLOWED_SINGLE_CHARS}]"


_KOREAN_ONLY_GRAMMAR = _build_korean_only_grammar()


class LlamaServerEngine:
    def __init__(
        self,
        base_url: str = config.LLAMA_SERVER_URL,
        timeout_s: float = config.LLAMA_SERVER_TIMEOUT_S,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def translate(
        self,
        text: str,
        *,
        fast: bool = False,
        context: str | None = None,
        glossary_hint: str | None = None,
    ) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="")

        system_prompt = _FAST_SYSTEM_PROMPT if fast else _FINAL_SYSTEM_PROMPT
        if glossary_hint:
            system_prompt = f"{system_prompt}\n\n{glossary_hint}"
        user_content = (
            f"[PREVIOUS SENTENCE]\n{context}\n\n[TEXT TO TRANSLATE]\n{text}"
            if context
            else text
        )
        max_tokens = config.LLAMA_FAST_MAX_TOKENS if fast else config.LLAMA_FINAL_MAX_TOKENS

        response = await self._client.post(
            "/v1/chat/completions",
            json={
                "model": config.LLAMA_SERVER_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": max_tokens,
                # 0.0 (greedy) instead of a small positive temperature: the
                # user noticed re-running the exact same segment produced a
                # different sentence each time, and low-but-nonzero
                # temperature is also more prone to sampling into a rare/
                # wrong-script token before the grammar mask corrects it.
                "temperature": 0.0,
                "grammar": _KOREAN_ONLY_GRAMMAR,
                # Grammar-constrained decoding occasionally backs the model
                # into a corner (its preferred next token is masked out for
                # containing CJK) and it falls into repeating the same
                # allowed token/character until max_tokens — observed as
                # walls of a single repeated character in manual testing.
                # repeat_penalty discourages immediately re-emitting recent
                # tokens, which breaks that loop.
                "repeat_penalty": 1.3,
                "repeat_last_n": 64,
            },
        )
        response.raise_for_status()
        data = response.json()
        translated = data["choices"][0]["message"]["content"].strip()
        return TranslationResult(text=translated)

    async def aclose(self) -> None:
        await self._client.aclose()
