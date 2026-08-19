"""Heuristic check for whether a Japanese partial transcript reads as a
complete sentence — the "context/punctuation" correction stage from
CLAUDE.md's finalization strategy, kept decoupled from the silence-based
trigger in audio_session.py (see config.FINALIZE_GRACE_MS).

Deliberately a cheap regex heuristic, not a parser: it only needs to bias
the grace-period decision, not judge grammaticality precisely. A false
"complete" just falls back to today's silence-only behavior; a false
"incomplete" only costs one bounded grace period, never blocks
finalization outright (audio_session.py always force-finalizes once the
grace period elapses).
"""

from __future__ import annotations

import re

# Trailing continuation-expecting connectives that grammatically demand a
# following clause. Deliberately excludes て/で/し: those are also extremely
# common as casual/spoken sentence-enders in live-stream Japanese ("ちょっと
# 待ってて" trailing off naturally), so flagging them as "incomplete" added
# a felt ~400ms delay to a large fraction of ordinary sentences (found by
# the user testing live). Keep only connectives with little/no legitimate
# use as a sentence-final form.
_INCOMPLETE_TAIL_RE = re.compile(r"(たら|れば|ので|から|けど|けれど|けれども|のに|つつ)[ 、,]*$")

# Sentence-final forms / punctuation: if present, treat as complete even if
# an ambiguous connective also matches (checked first).
_COMPLETE_TAIL_RE = re.compile(
    r"(です|ます|でした|ました|だ|よ|ね|わ|の|な|ぞ|さ|かな|かも|[。！？])[ 、,]*$"
)


def looks_complete(text: str) -> bool:
    """True unless the tail gives positive evidence the sentence is
    still in progress. Empty/unrecognized input defaults to True — we
    only want to *delay* finalization when we have a concrete reason
    to, not add latency to the common case."""
    stripped = text.strip()
    if not stripped:
        return True
    if _COMPLETE_TAIL_RE.search(stripped):
        return True
    if _INCOMPLETE_TAIL_RE.search(stripped):
        return False
    return True


# Stricter subset of _COMPLETE_TAIL_RE for proactively splitting a run-on
# speaker's buffer mid-speech (audio_session.py, no pause detected at all).
# Unlike looks_complete(), this must default to False on anything
# ambiguous: casual particles like よ/ね/わ/かな are also used to trail off
# mid-sentence, and defaulting True here (as looks_complete does) would
# chop on every such filler instead of only on unambiguous sentence ends —
# worse than not splitting, since it fragments translation context instead
# of preserving it.
_STRONG_COMPLETE_TAIL_RE = re.compile(r"(です|ます|でした|ました|だ|[。！？])[ 、,]*$")

# Real terminal punctuation is checked ANYWHERE in the buffer, not just at
# the tail (see _STRONG_COMPLETE_TAIL_RE above and looks_complete below,
# both tail-only). A run-on speaker with no pause can add a whole new clause
# within a single PARTIAL_UPDATE_INTERVAL_S cycle, pushing an already-
# complete sentence's 。！？ out of tail position before a tail-only check
# ever sees it — observed live: the utterance then keeps growing unsplit
# until MAX_UTTERANCE_SECONDS forces it, chopped at whatever word the
# speaker happened to be on rather than the sentence break they actually
# made, and the oversized/misaligned buffer then translates badly. 。！？ is
# unambiguous even mid-string (unlike です/ます, which can appear inside a
# longer construction), so scanning the whole buffer for it is safe.
_TERMINAL_PUNCTUATION_RE = re.compile(r"[。！？]")


def has_strong_sentence_boundary(text: str) -> bool:
    """True when the buffer contains unambiguous sentence-final punctuation
    anywhere, or the tail is a plain polite/copula verb ending — the bar for
    checkpointing a still-ongoing (no-pause) speaker mid-utterance."""
    stripped = text.strip()
    if _TERMINAL_PUNCTUATION_RE.search(stripped):
        return True
    return bool(_STRONG_COMPLETE_TAIL_RE.search(stripped))
