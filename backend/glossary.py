"""User-editable proper-noun glossary — fixes channel-specific mistranslations
(streamer/character names the STT model has never seen and the translation
model has no reason to render consistently), applied at two points:

  - STT: every glossary source term is fed to faster-whisper as an
    initial_prompt vocabulary hint (a soft bias, not a hard constraint —
    it nudges the decoder toward spellings it's been shown, unlike the
    GBNF-grammar constraint used for translation script-safety).
  - Translation: whichever glossary entries actually appear in a given STT
    result are surfaced as an explicit "translate these names exactly like
    this" instruction, so the LLM doesn't have to guess a rendering.

Edit backend/glossary.json directly: {"source term (JP)": "target term (KO)"}.
"""

from __future__ import annotations

import json
from pathlib import Path

_GLOSSARY_PATH = Path(__file__).parent / "glossary.json"


class Glossary:
    def __init__(self, entries: dict[str, str]) -> None:
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    @classmethod
    def load(cls, path: Path = _GLOSSARY_PATH) -> "Glossary":
        if not path.exists():
            return cls({})
        with path.open(encoding="utf-8") as f:
            entries = json.load(f)
        return cls(entries)

    @property
    def whisper_hint(self) -> str:
        """Vocabulary bias string for faster-whisper's initial_prompt."""
        return ", ".join(self._entries.keys())

    def match(self, text: str) -> list[tuple[str, str]]:
        return [(src, tgt) for src, tgt in self._entries.items() if src in text]

    def translation_hint(self, text: str) -> str | None:
        """A short instruction listing only entries relevant to this text,
        so unrelated glossary terms don't bloat/confuse every prompt."""
        matches = self.match(text)
        if not matches:
            return None
        pairs = "; ".join(f"{src} -> {tgt}" for src, tgt in matches)
        return f"These proper nouns must be translated exactly as follows: {pairs}."
