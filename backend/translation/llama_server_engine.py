"""llama-server (OpenAI-compatible /v1/chat/completions) translation engine.

Chosen over Ollama: llama-server is the same llama.cpp engine Ollama wraps,
without Ollama's ~10-30% single-stream overhead (see docs/planning/PRD.md
benchmarking notes for sources). Swappable behind TranslationEngine —
dropping in vLLM/ExLlamaV2/etc later means writing a new class here, no
changes to audio_session.py.
"""

from __future__ import annotations

import asyncio
import logging
import re

import httpx

from backend import config
from backend.translation import prompts
from backend.translation.base import TranslationResult

logger = logging.getLogger("live-translator.backend")

# Grammar-constrained decoding: restrict the output alphabet to an explicit
# ALLOW-list (Hangul + digits + general punctuation) rather than a blacklist
# of "known bad" scripts. A blacklist is whack-a-mole — after blocking CJK
# script leakage, manual testing turned up Cyrillic leaking through the same
# way (nothing was excluding it). A whitelist closes off every script we
# didn't explicitly allow, so no further script-specific patches should be
# needed. This guarantees script correctness only, not translation accuracy.
#
# Latin letters (A-Z/a-z) are deliberately NOT in the whitelist, despite the
# grammar being nominally about *script* purity rather than *language*
# purity. Manual review of a real transcript turned up the model falling
# back to whole raw English words/phrases mid-sentence on hard segments
# (repetitive filler, unclear STT) — "workplace", "phew", "maybe gonna do
# it.", even stray "-END-"/"START." markers — because English was an
# allowed escape hatch under grammar pressure. Blocking Latin letters forces
# a Hangul transliteration instead of a language-level cop-out.
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
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0xAC00, 0xD7A3),  # Hangul Syllables
    (0x2000, 0x206F),  # General Punctuation (curly quotes, dashes, ellipsis, etc.)
]
# Space + a conservative punctuation set actually needed in a Korean
# sentence. '-' MUST stay last in the character class (unescaped '-'
# anywhere else would be read as a range operator).
_ALLOWED_SINGLE_CHARS = " .,!?~'\"()-"


def _gbnf_literal(s: str) -> str:
    """Quote a fixed string as a GBNF literal (escape backslash/quote)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _build_korean_only_grammar(extra_literals: tuple[str, ...] = ()) -> str:
    """Korean-only script grammar, with an optional set of exact-match
    literal exceptions (e.g. a glossary target like "TY" that's meant to
    stay in Latin script — see Glossary.latin_targets). Each literal is an
    alternative "word" the grammar accepts verbatim; every other character
    still has to come from the Hangul-only safe-char set. Built fresh per
    request only when extra_literals is non-empty (the common case reuses
    the cached _KOREAN_ONLY_GRAMMAR below).
    """
    ranges = "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _ALLOWED_SCRIPT_RANGES)
    literal_alts = "".join(f" | {_gbnf_literal(lit)}" for lit in extra_literals if lit)
    return (
        "root ::= segment+\n"
        f"segment ::= safe-char+{literal_alts}\n"
        f"safe-char ::= [{ranges}{_ALLOWED_SINGLE_CHARS}]"
    )


_KOREAN_ONLY_GRAMMAR = _build_korean_only_grammar()

# Script-forcing markup (2026-08-20): the viewer can wrap a Korean word in
# '단일따옴표' to force hiragana or "겹따옴표" to force katakana in the
# translated output (e.g. for a streamer's name/nickname where the "natural"
# orthography choice a general translation model makes is a coin flip and
# often wrong). Asking the main translate_ko_to_ja call to both pick the
# right script AND weave it naturally into a full sentence under one prompt
# proved unreliable in practice (it kept collapsing to hiragana regardless,
# and sometimes added its own 「」 brackets around the span) — so the actual
# script choice is resolved *first*, in its own tiny grammar-constrained
# request per span (same technique as _build_korean_only_grammar below: a
# GBNF character-class mask makes the wrong script physically unable to
# decode, rather than hoping the model complies with a prompt instruction).
# The main call then only has to copy a literal marked-off substring
# verbatim, which is a far easier instruction to follow than "pick hiragana
# here, katakana there, mid-sentence."
_QUOTE_SPAN_RE = re.compile(r"'([^']+)'|\"([^\"]+)\"")

# English proper nouns (band/artist/game names etc.) typed directly in the
# Korean input are common in real streamer chat and are normally left in
# Latin script as-is rather than katakana-ized — matches real Japanese chat
# convention. Single contiguous Latin-letter token (optionally trailing
# digits), not a general "any English" matcher.
_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_KANA_SINGLE_CHARS = "ー "  # long-vowel mark + space; script-neutral, allowed either way


def _build_single_script_grammar(lo: int, hi: int) -> str:
    return f"root ::= safe-char+\nsafe-char ::= [{chr(lo)}-{chr(hi)}{_KANA_SINGLE_CHARS}]"


_HIRAGANA_ONLY_GRAMMAR = _build_single_script_grammar(0x3040, 0x309F)
_KATAKANA_ONLY_GRAMMAR = _build_single_script_grammar(0x30A0, 0x30FF)

# Japanese script allow-list, same rationale as _ALLOWED_SCRIPT_RANGES above
# (a whitelist closes every script we didn't explicitly allow, instead of
# chasing individual leaked scripts one at a time).
_JAPANESE_SCRIPT_RANGES = [
    (0x0030, 0x0039),  # digits
    (0x3040, 0x309F),  # Hiragana
    (0x30A0, 0x30FF),  # Katakana
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs (kanji)
    (0x3000, 0x303F),  # CJK punctuation (。、「」etc.)
    (0xFF01, 0xFF60),  # Fullwidth punctuation/forms
]
# '-' MUST stay last in the character class (same rule as
# _ALLOWED_SINGLE_CHARS above) — it was previously placed before 'ー',
# which GBNF read as a range operator spanning ')' (U+0029) through 'ー'
# (U+30FC), silently admitting nearly the entire Latin/ASCII block despite
# the whole point of this grammar being to exclude it. Reproduced live: a
# Korean chat message containing an English word ("SUPERCELL") came back
# with that word romanized verbatim in the "translation" instead of being
# rendered as a Japanese-script loanword.
_JAPANESE_SINGLE_CHARS = " .,!?~'\"()ー-"


def _build_japanese_only_grammar(extra_literals: tuple[str, ...] = ()) -> str:
    """`extra_literals` are exact-match Latin-script exceptions (e.g. a band
    name typed in English in the Korean input — see _LATIN_WORD_RE) that
    stay in Latin script verbatim rather than being forced into katakana,
    same mechanism as _build_korean_only_grammar's extra_literals above."""
    ranges = "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _JAPANESE_SCRIPT_RANGES)
    literal_alts = "".join(f" | {_gbnf_literal(lit)}" for lit in extra_literals if lit)
    return (
        "root ::= segment+\n"
        f"segment ::= safe-char+{literal_alts}\n"
        f"safe-char ::= [{ranges}{_JAPANESE_SINGLE_CHARS}]"
    )


_JAPANESE_ONLY_GRAMMAR = _build_japanese_only_grammar()


def _match_source_punctuation(source: str, translated: str) -> str:
    """Strip ！/! or ？/? from `translated` when `source` (the user's raw
    Korean input) has no corresponding !/? at all — the prompt instruction
    to only add emphasis punctuation the user actually typed proved
    unreliable in live testing (the model kept adding a ！ to plain
    single-word input regardless), so enforce it here instead."""
    if "!" not in source and "！" not in source:
        translated = translated.replace("！", "").replace("!", "")
    if "?" not in source and "？" not in source:
        translated = translated.replace("？", "").replace("?", "")
    return translated


class LlamaServerEngine:
    def __init__(
        self,
        base_url: str = config.LLAMA_SERVER_URL,
        timeout_s: float = config.LLAMA_SERVER_TIMEOUT_S,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def verify_contract(self) -> None:
        """Startup probe: server reachable + honors GBNF grammar.

        The grammar below only admits the single string "가" — a server that
        actually enforces GBNF cannot return anything else. Any other output
        means the `grammar` field was silently ignored (e.g. a non-llama.cpp
        OpenAI-compatible server), i.e. Korean-only script enforcement is
        inactive. Warn loudly either way instead of failing startup — the
        server is allowed to come up after the backend does.
        """
        try:
            response = await self._client.post(
                "/v1/chat/completions",
                json={
                    "model": config.LLAMA_SERVER_MODEL,
                    "messages": [{"role": "user", "content": "1+1=?"}],
                    "max_tokens": 4,
                    "temperature": 0.0,
                    "grammar": 'root ::= "가"',
                },
            )
            response.raise_for_status()
            out = response.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            logger.warning(
                "translation server unreachable at startup — start it and/or check %s",
                config.LLAMA_SERVER_URL,
            )
            return
        if out != "가":
            logger.warning(
                "translation server does NOT honor GBNF grammar (probe returned %r) — "
                "Korean-only script enforcement is INACTIVE. Is this a llama.cpp server?",
                out,
            )
        else:
            logger.info("translation server contract verified (GBNF grammar honored)")

    async def translate(
        self,
        text: str,
        *,
        fast: bool = False,
        context: str | None = None,
        context_translation: str | None = None,
        glossary_hint: str | None = None,
        use_grammar: bool = True,
        use_repeat_penalty: bool = True,
        allowed_literals: tuple[str, ...] = (),
    ) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="")

        system_prompt = prompts.FAST_SYSTEM_PROMPT if fast else prompts.FINAL_SYSTEM_PROMPT
        # Everything request-specific goes into the user message, never the
        # system prompt — llama.cpp server reuses the KV cache for the longest
        # unchanged prompt prefix, and the (long) system prompt only stays
        # cacheable if it is byte-identical across requests.
        sections = []
        if glossary_hint:
            sections.append(f"[GLOSSARY]\n{glossary_hint}")
        if context:
            sections.append(f"[PREVIOUS SENTENCE]\n{context}")
            if context_translation:
                sections.append(f"[PREVIOUS TRANSLATION]\n{context_translation}")
        if sections:
            sections.append(f"[TEXT TO TRANSLATE]\n{text}")
            user_content = "\n\n".join(sections)
        else:
            user_content = text
        max_tokens = config.LLAMA_FAST_MAX_TOKENS if fast else config.LLAMA_FINAL_MAX_TOKENS

        request_json = {
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
        }
        if use_grammar:
            request_json["grammar"] = (
                _build_korean_only_grammar(allowed_literals)
                if allowed_literals
                else _KOREAN_ONLY_GRAMMAR
            )
        if use_repeat_penalty:
            # Grammar-constrained decoding occasionally backs the model
            # into a corner (its preferred next token is masked out for
            # containing CJK) and it falls into repeating the same
            # allowed token/character until max_tokens — observed as
            # walls of a single repeated character in manual testing.
            # repeat_penalty discourages immediately re-emitting recent
            # tokens, which breaks that loop. NOTE: on Qwen3-14B this
            # backfires badly — combined with the grammar mask it collapses
            # into verbatim-echoing the system prompt instead of
            # translating (see docs/eval/MODEL_BENCHMARK_PLAN.md); keep it off
            # for that model family.
            request_json["repeat_penalty"] = 1.3
            request_json["repeat_last_n"] = 64

        response = await self._client.post(
            "/v1/chat/completions",
            json=request_json,
            timeout=config.LLAMA_FAST_TIMEOUT_S if fast else config.LLAMA_SERVER_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        timings = data.get("timings")
        if timings:
            logger.debug(
                "llm timings fast=%s prompt_ms=%.0f predicted_ms=%.0f",
                fast,
                timings.get("prompt_ms", -1),
                timings.get("predicted_ms", -1),
            )
        translated = data["choices"][0]["message"]["content"].strip()
        return TranslationResult(text=translated)

    async def translate_ko_to_ja(
        self,
        text: str,
        *,
        context: str | None = None,
    ) -> TranslationResult:
        """Draft (2026-08-20), see base.py's docstring. Deliberately a
        separate method rather than a `direction` flag on translate() — the
        forward (JA->KO) path above is heavily tuned (glossary, false-friend
        notes, laughter markers, KV-cache-friendly prompt structure) and
        this reverse direction hasn't been through any of that yet; keeping
        them apart avoids accidentally coupling future JA->KO tuning to
        this still-unvalidated direction, or vice versa."""
        if not text.strip():
            return TranslationResult(text="")

        # Bare-span fast path (2026-08-20): when the *entire* message is
        # nothing but one forced-kana span (e.g. the user just typed "유노"
        # to check how a name renders), the full generative call below
        # reliably ignored the 【 】-marked content and improvised its own
        # (wrong-script) nickname instead — reproduced consistently live.
        # With no surrounding sentence to translate, there's nothing for the
        # main call to usefully add anyway, so skip it and return the
        # already-correct, grammar-verified kana straight from the per-span
        # transliteration step.
        bare_match = _QUOTE_SPAN_RE.fullmatch(text.strip())
        if bare_match:
            hiragana_span, katakana_span = bare_match.group(1), bare_match.group(2)
            if hiragana_span is not None:
                return TranslationResult(text=await self._transliterate_forced(hiragana_span, "hiragana"))
            return TranslationResult(text=await self._transliterate_forced(katakana_span, "katakana"))

        resolved_text, required_kana = await self._resolve_forced_kana_spans(text)
        # Dedup preserving order — an English word repeated in the input
        # only needs one grammar alternative for it.
        english_literals = tuple(dict.fromkeys(_LATIN_WORD_RE.findall(text)))
        grammar = (
            _build_japanese_only_grammar(english_literals) if english_literals else _JAPANESE_ONLY_GRAMMAR
        )

        sections = []
        if context:
            sections.append(f"[BROADCAST CONTEXT]\n{context}")
        sections.append(f"[TEXT TO TRANSLATE]\n{resolved_text}")
        user_content = "\n\n".join(sections)

        async def run_once() -> str:
            request_json = {
                "model": config.LLAMA_SERVER_MODEL,
                "messages": [
                    {"role": "system", "content": prompts.KO_JA_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                "max_tokens": config.LLAMA_FINAL_MAX_TOKENS,
                "temperature": 0.0,
                "grammar": grammar,
                "repeat_penalty": 1.3,
                "repeat_last_n": 64,
            }
            response = await self._client.post(
                "/v1/chat/completions",
                json=request_json,
                timeout=config.LLAMA_SERVER_TIMEOUT_S,
            )
            response.raise_for_status()
            data = response.json()
            out = data["choices"][0]["message"]["content"].strip()
            # Safety net for the (rare) case the model reproduces the marker
            # brackets themselves despite the instruction to drop them —
            # plain deletion rather than re-prompting, since a stray 【】 in
            # casual stream chat is a cosmetic nit, not a script-purity
            # violation the grammar mask would already have prevented.
            return out.replace("【", "").replace("】", "")

        translated = await run_once()
        if required_kana and not all(kana in translated for kana in required_kana):
            # llama.cpp's continuous batching makes even greedy
            # (temperature=0) decoding non-deterministic across requests —
            # observed live losing/merging a marked span. One retry is
            # cheap and sometimes lands differently; if it still doesn't
            # stick, log it rather than looping (this is still a draft
            # direction with no dedicated eval harness — see the class
            # docstring on translate_ko_to_ja).
            logger.warning(
                "ko->ja forced-kana span(s) missing from output, retrying once: required=%r got=%r",
                required_kana,
                translated,
            )
            translated = await run_once()
            if not all(kana in translated for kana in required_kana):
                logger.warning(
                    "ko->ja forced-kana span(s) still missing after retry: required=%r got=%r",
                    required_kana,
                    translated,
                )
        return TranslationResult(text=_match_source_punctuation(text, translated))

    async def _resolve_forced_kana_spans(self, text: str) -> tuple[str, list[str]]:
        """Replace each '...'/"..." span in `text` with its already-resolved
        hiragana/katakana rendering, marked with 【】 — see the comment above
        _QUOTE_SPAN_RE for why this is a separate grammar-constrained request
        per span rather than one prompt instruction on the main call. Also
        returns the resolved kana strings so the caller can verify they
        actually survived into the final translation."""
        matches = list(_QUOTE_SPAN_RE.finditer(text))
        if not matches:
            return text, []

        async def render(match: re.Match) -> str:
            hiragana_span, katakana_span = match.group(1), match.group(2)
            if hiragana_span is not None:
                return await self._transliterate_forced(hiragana_span, "hiragana")
            return await self._transliterate_forced(katakana_span, "katakana")

        rendered = await asyncio.gather(*(render(m) for m in matches))

        parts = []
        cursor = 0
        for match, kana in zip(matches, rendered):
            parts.append(text[cursor : match.start()])
            parts.append(f"【{kana}】")
            cursor = match.end()
        parts.append(text[cursor:])
        return "".join(parts), list(rendered)

    async def _transliterate_forced(self, korean_span: str, script: str) -> str:
        grammar = _HIRAGANA_ONLY_GRAMMAR if script == "hiragana" else _KATAKANA_ONLY_GRAMMAR
        request_json = {
            "model": config.LLAMA_SERVER_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Transliterate the Korean word or name '{korean_span}' into how "
                        f"a Japanese speaker would phonetically read/write it, using ONLY "
                        f"{script} characters. Output ONLY the {script} text, nothing "
                        "else — no romanization, no notes, no punctuation."
                    ),
                }
            ],
            "max_tokens": 24,
            "temperature": 0.0,
            "grammar": grammar,
        }
        response = await self._client.post(
            "/v1/chat/completions",
            json=request_json,
            timeout=config.LLAMA_SERVER_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    async def summarize_context(self, ja_history: str) -> str:
        if not ja_history.strip():
            return ""
        request_json = {
            "model": config.LLAMA_SERVER_MODEL,
            "messages": [
                {"role": "system", "content": prompts.CONTEXT_SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": ja_history},
            ],
            "max_tokens": 48,
            "temperature": 0.0,
            "grammar": _KOREAN_ONLY_GRAMMAR,
            "repeat_penalty": 1.3,
            "repeat_last_n": 64,
        }
        response = await self._client.post(
            "/v1/chat/completions",
            json=request_json,
            timeout=config.LLAMA_SERVER_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    async def aclose(self) -> None:
        await self._client.aclose()
