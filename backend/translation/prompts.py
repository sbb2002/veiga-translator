"""System prompts for LlamaServerEngine, loaded from prompts.yaml.

Split out of llama_server_engine.py (2026-08-20) so the prompt text itself —
the thing that actually gets iterated on tuning-session after
tuning-session — lives somewhere it can be edited as plain prose, no Python
string-escaping or `+` concatenation. Moved from inline .py string constants
to YAML for the same reason. Grammar/decoding-constraint logic (GBNF
character classes, the regexes that detect quoted/Latin spans) stays in
llama_server_engine.py since that isn't prompt text, just request-building
code that happens to live next to it.

`notes` in the YAML are reusable blocks spliced into more than one forward-
direction (JA->KO) system prompt below — see _assemble(). The
context-summary and KO->JA prompts are standalone, used as-is.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_data = yaml.safe_load((Path(__file__).parent / "prompts.yaml").read_text(encoding="utf-8"))
_notes = _data["notes"]


def _assemble(intro: str, *note_keys: str, outro: str) -> str:
    parts = [intro, *(_notes[key] for key in note_keys), outro]
    return " ".join(part.strip() for part in parts)


# Shared across both forward-direction prompts below — keep in one place so
# adding/removing a note updates both instead of risking them drifting out
# of sync with each other.
_FORWARD_NOTE_KEYS = (
    "slang",
    "scat_singing",
    "filler",
    "honorific",
    "false_friend",
    "laughter",
    "connotation",
    "no_english",
    "glossary_section",
)

FAST_SYSTEM_PROMPT = _assemble(
    _data["fast_system_prompt_intro"],
    *_FORWARD_NOTE_KEYS,
    outro=_data["fast_system_prompt_outro"],
)

FINAL_SYSTEM_PROMPT = _assemble(
    _data["final_system_prompt_intro"],
    "continuity",
    *_FORWARD_NOTE_KEYS,
    outro=_data["final_system_prompt_outro"],
)

CONTEXT_SUMMARY_SYSTEM_PROMPT = _data["context_summary_system_prompt"].strip()
KO_JA_SYSTEM_PROMPT = _data["ko_ja_system_prompt"]
