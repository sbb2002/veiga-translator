"""User-editable proper-noun glossary — fixes channel-specific mistranslations
(streamer/character names the STT model has never seen and the translation
model has no reason to render consistently), applied at two points:

  - STT: glossary source terms are intentionally NOT wired as faster-whisper
    hotwords/initial_prompt hints (see the startup comment in backend/main.py —
    hotwords measurably increased hallucinations). Glossary matching happens
    only at translation time.
  - Translation: whichever glossary entries actually appear in a given STT
    result are surfaced as an explicit "translate these names exactly like
    this" instruction, so the LLM doesn't have to guess a rendering.

Edit backend/glossary.json directly: {"source term (JP)": "target term (KO)"}.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_GLOSSARY_PATH = Path(__file__).parent / "glossary.json"
_HAS_LATIN_RE = re.compile(r"[A-Za-z]")


class Glossary:
    def __init__(self, entries: dict[str, str]) -> None:
        # Source keys are NFKC-normalized so matching is robust to width/compatibility
        # variants in STT output; targets stay verbatim (they go into prompts/grammar as-is).
        self._entries = {
            unicodedata.normalize("NFKC", src): tgt for src, tgt in entries.items()
        }

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
        normalized = unicodedata.normalize("NFKC", text)
        return [(src, tgt) for src, tgt in self._entries.items() if src in normalized]

    def latin_targets(self, text: str) -> tuple[str, ...]:
        """Latin-script target terms (e.g. a streamer handle like "TY")
        among the glossary entries matched in `text` — passed to
        LlamaServerEngine.translate() as `allowed_literals` so the
        Korean-only grammar mask carves out an exact-match exception for
        these specific strings instead of blocking Latin script outright.
        """
        return tuple(tgt for _src, tgt in self.match(text) if _HAS_LATIN_RE.search(tgt))

    def translation_hint(self, text: str) -> str | None:
        """A short instruction listing only entries relevant to this text,
        so unrelated glossary terms don't bloat/confuse every prompt."""
        matches = self.match(text)
        if not matches:
            return None
        pairs = "; ".join(f"{src} -> {tgt}" for src, tgt in matches)
        return f"These proper nouns must be translated exactly as follows: {pairs}."


if __name__ == "__main__":
    g = Glossary({"ティーワイ": "TY"})
    # Half-width katakana input must match the full-width glossary key via NFKC.
    assert g.match("ﾃｨｰﾜｲです"), "NFKC match failed"
    assert g.latin_targets("ティーワイ") == ("TY",)
    assert g.translation_hint("無関係な文") is None
    print("glossary self-check OK")
