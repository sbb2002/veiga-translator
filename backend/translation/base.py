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
        fast: bool = False,  # True => provisional pass: literal/quick is fine
        context: str | None = None,
        glossary_hint: str | None = None,
    ) -> TranslationResult: ...
