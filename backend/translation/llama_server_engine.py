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
    "It may also contain descriptive mimetic words (擬音語/擬態語) that "
    "characterize a sound or quality rather than naming a real object — "
    "e.g. キンキン describing a shrill/piercing high voice. Never "
    "transliterate these phonetically into Korean syllables that happen to "
    "sound similar (e.g. キンキン is NOT '금금거리다', which is not a real "
    "Korean word/expression) — instead render the *quality being "
    "described* with a natural Korean descriptive phrase (e.g. '귀를 "
    "찌르는', '쨍쨍한', '새된' for a piercing high voice). Infer the "
    "intended real meaning from context and translate it naturally into "
    "the closest natural Korean word or interjection — do not "
    "transliterate literally, and never invent a nonsense word. Also, "
    "common slang/filler words are sometimes stretched or emphasized for "
    "effect (extra long vowels via ー, repeated mora, e.g. ガーッチ for "
    "ガチ) — recognize these as the same base word and translate its "
    "actual meaning (e.g. ガーッチ = ガチ = '진짜로'/'제대로', an emphasis "
    "intensifier), never invent a new nonsense word just because of the "
    "stretched spelling (e.g. ガーッチ is NOT '가르치', which means 'teach' "
    "and is unrelated)."
)

_LAUGHTER_NOTE = (
    "A trailing 'w', 'ww', 'www' etc. (from 笑う, 'to laugh') is Japanese "
    "internet/streamer shorthand for laughter — the direct equivalent of "
    "Korean 'ㅋㅋㅋ'. It is real content, not disfluency noise to clean up: "
    "always keep it in the output as 'ㅋㅋㅋ' (scale the number of ㅋ "
    "roughly with how many w's there are), even when producing a polished "
    "'natural, fluent' final translation — do not drop it for the sake of "
    "a cleaner-looking sentence."
)

_CONNOTATION_NOTE = (
    "ずるい literally means 'unfair/cheating' but is very often used as a "
    "lighthearted admiring exclamation about someone else's ability or "
    "charm — closer to '(그렇게 잘하면) 반칙이지'/'부럽다'/'얄밉다' in Korean — "
    "especially when the surrounding context is clearly praise/envy rather "
    "than an actual accusation of unfairness. In that admiring context, "
    "translate it as '부럽다'/'얄밉다' or similar, NOT as a harsh negative "
    "judgment like '야비하다'(despicable) or '억지'(unreasonable) — those "
    "carry moral condemnation that isn't there in the affectionate usage, "
    "and keep the rendering consistent across repeated occurrences of the "
    "same word in the same conversation rather than drifting more negative "
    "each time."
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

_FALSE_FRIEND_NOTE = (
    "Watch for Japanese words that happen to sound like an unrelated real "
    "Korean word — do not substitute the Korean look-alike just because it "
    "exists. In particular: やばい is a versatile interjection meaning "
    "roughly 'crazy/insane/awesome/terrible/dangerous' depending on context "
    "(never translate it as '야바위', the Korean word for a scam/con game — "
    "that is an unrelated false friend); render it as a natural Korean "
    "interjection fitting the context (e.g. '헐', '미쳤다', '큰일났다', "
    "'대박'). Also, a trailing 嘘 (uso) right after a statement is usually "
    "the speaker tagging their own previous sentence as a joke/retraction "
    "('just kidding'), not a literal claim that something 'is a lie' — "
    "translate it as a natural Korean self-correcting tag like '농담이야'/"
    "'아니 뭐'/'거짓말이고' used the same way, based on what reads naturally, "
    "rather than a flat statement like '~라는 게 거짓말이다'."
)

_NO_ENGLISH_NOTE = (
    "The output must be written entirely in Hangul (plus digits and basic "
    "punctuation) — never fall back to an English word or phrase, even for "
    "a fragment you find hard to translate or an interjection like a laugh. "
    "Every English/foreign word must be rendered as its closest Korean "
    "equivalent or a Hangul transliteration, never left in Latin script."
)

_FAST_SYSTEM_PROMPT = (
    "Translate the following Japanese text fragment into Korean. The fragment "
    "may be an incomplete sentence that is still being spoken. Translate every "
    "word into Korean, even if the fragment is ambiguous — use your best-guess "
    "Korean rendering (a Korean loanword approximation is fine) rather than "
    "leaving anything untranslated. " + _SLANG_NOTE + " " + _FILLER_NOTE + " "
    + _HONORIFIC_NOTE + " " + _FALSE_FRIEND_NOTE + " " + _LAUGHTER_NOTE
    + " " + _CONNOTATION_NOTE
    + " " + _NO_ENGLISH_NOTE + " Output ONLY the Korean translation, "
    "nothing else — no notes, no romanization, no quotes."
)

_CONTINUITY_NOTE = (
    "The user message may also include '[PREVIOUS SENTENCE]' (the most "
    "recent Japanese sentences spoken before this one) and '[PREVIOUS "
    "TRANSLATION]' (how you rendered each in Korean) — when there is more "
    "than one, they are numbered oldest-first, ending right where the "
    "current fragment picks up. These are VAD-based utterance splits, not "
    "necessarily complete sentences — live/casual speech is routinely cut "
    "mid-thought (a speaker trailing off, dropping a subject/pronoun that "
    "was already established a couple of sentences back, or correcting/"
    "reversing themselves across a split, e.g. 'X ... actually not X'). "
    "Read them only to resolve that kind of ambiguity in the current "
    "fragment — a missing subject, an unclear referent, or a correction/"
    "reversal in progress — and translate this fragment so it reads as a "
    "natural continuation of the most recent [PREVIOUS TRANSLATION] line, "
    "matching its tone and honorific level (letting a correction/reversal "
    "actually land as one, not flatly contradicting a walk-back the speaker "
    "is clearly making). Never translate or repeat any [PREVIOUS SENTENCE]/"
    "[PREVIOUS TRANSLATION] line in your output — output ONLY the "
    "translation of the text under '[TEXT TO TRANSLATE]'."
)

_FINAL_SYSTEM_PROMPT = (
    "You are a professional Japanese-to-Korean translator. Translate the "
    "complete Japanese sentence under the '[TEXT TO TRANSLATE]' heading into "
    "natural, fluent, idiomatic Korean, as it would be spoken or subtitled. "
    + _CONTINUITY_NOTE + " " + _SLANG_NOTE + " " + _FILLER_NOTE + " "
    + _HONORIFIC_NOTE + " " + _FALSE_FRIEND_NOTE + " " + _LAUGHTER_NOTE
    + " " + _CONNOTATION_NOTE
    + " " + _NO_ENGLISH_NOTE + " Output ONLY the Korean translation of "
    "that text, nothing else — no notes, no romanization, no quotes."
)

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
        context_translation: str | None = None,
        glossary_hint: str | None = None,
        use_grammar: bool = True,
        use_repeat_penalty: bool = True,
        allowed_literals: tuple[str, ...] = (),
    ) -> TranslationResult:
        if not text.strip():
            return TranslationResult(text="")

        system_prompt = _FAST_SYSTEM_PROMPT if fast else _FINAL_SYSTEM_PROMPT
        if glossary_hint:
            system_prompt = f"{system_prompt}\n\n{glossary_hint}"
        if context:
            prev_translation_section = (
                f"\n\n[PREVIOUS TRANSLATION]\n{context_translation}" if context_translation else ""
            )
            user_content = (
                f"[PREVIOUS SENTENCE]\n{context}{prev_translation_section}"
                f"\n\n[TEXT TO TRANSLATE]\n{text}"
            )
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
            # translating (see docs/MODEL_BENCHMARK_PLAN.md); keep it off
            # for that model family.
            request_json["repeat_penalty"] = 1.3
            request_json["repeat_last_n"] = 64

        response = await self._client.post("/v1/chat/completions", json=request_json)
        response.raise_for_status()
        data = response.json()
        translated = data["choices"][0]["message"]["content"].strip()
        return TranslationResult(text=translated)

    async def aclose(self) -> None:
        await self._client.aclose()
