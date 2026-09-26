"""Cost and latency gate.

Decides whether a turn is worth a reasoner call. It never assigns a commitment
status, never labels acknowledgements, and never chooses SILENT vs CLARIFY as a
semantic outcome. Callers that skip here must not invent NO_COMMITMENT.
"""

from __future__ import annotations

import re

from intelligence.models import FilterDecision

# Bound prompt size. Longer turns still proceed; they are truncated.
MAX_CHARS = 4000

# Pure disfluencies. Dropped to save a model call, not classified.
_FILLER = frozenset({"uh", "um", "hmm", "hm", "er", "ah", "eh"})

_WS = re.compile(r"\s+")
_TRAIL = re.compile(r"[.!?]+$")


def normalize(text: str) -> str:
    return _WS.sub(" ", text.strip().lower())


def _duplicate_key(text: str) -> str:
    """Cost-gate key. Trailing punctuation does not make a new model call."""
    return _TRAIL.sub("", normalize(text)).strip()


def gate(
    text: str,
    *,
    previous_text: str | None = None,
    previous_speaker: str | None = None,
    speaker_role: str | None = None,
    max_chars: int = MAX_CHARS,
) -> FilterDecision:
    """Return whether to spend a reasoner call.

    Skip reasons: empty, no alphanumeric content, filler, or an immediate
    duplicate from the same speaker. Truncation still proceeds.
    """
    original = text or ""
    stripped = original.strip()
    if not stripped or not re.search(r"[A-Za-z0-9]", stripped):
        return FilterDecision(
            proceed=False,
            reason="empty",
            text="",
            original_chars=len(original),
        )

    norm = normalize(stripped)
    if norm in _FILLER:
        return FilterDecision(
            proceed=False,
            reason="filler",
            text=stripped,
            original_chars=len(original),
        )

    if (
        previous_text is not None
        and speaker_role is not None
        and speaker_role == previous_speaker
        and _duplicate_key(previous_text) == _duplicate_key(stripped)
    ):
        return FilterDecision(
            proceed=False,
            reason="duplicate",
            text=stripped,
            original_chars=len(original),
        )

    if len(stripped) > max_chars:
        return FilterDecision(
            proceed=True,
            reason="truncated",
            text=stripped[:max_chars],
            original_chars=len(original),
        )

    return FilterDecision(
        proceed=True,
        reason="pass",
        text=stripped,
        original_chars=len(original),
    )
