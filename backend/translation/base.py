"""Swappable translation engine interface.

Mirrors STTEngine (see stt/base.py) for the same reason: benchmark
candidates before committing (CLAUDE.md "STT / translation engine" policy).
Unlike STTEngine, this is async-native rather than sync+to_thread — a
translation call is a local HTTP request to another process (llama-server
today), not a blocking in-process compute call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class TranslationResult:
    text: str


class TranslationEngine(Protocol):
    async def translate(
        self,
        text: str,
        *,
        fast: bool = False,  # True => partial pass: literal/quick is fine
        context: str | None = None,
        glossary_hint: str | None = None,
    ) -> TranslationResult: ...

    async def translate_ko_to_ja(
        self,
        text: str,
        *,
        context: str | None = None,
    ) -> TranslationResult:
        """Reverse direction (draft, 2026-08-20): a viewer's own Korean chat
        message -> natural Japanese, for pasting into the stream's chat.
        `context` is the same recent-broadcast Japanese speech history used
        by the forward direction (just the JA side — the viewer doesn't need
        their own translated captions echoed back), so the outgoing message
        can be phrased to fit what's currently happening on stream."""
        ...

    async def summarize_context(self, ja_history: str) -> str:
        """One-line Korean summary of what's currently being talked about on
        stream, from recent final JA speech (same text as translate()'s
        `context` argument) — shown in the extension UI so a viewer glancing
        at the panel gets the gist without reading the whole scrolling log."""
        ...
