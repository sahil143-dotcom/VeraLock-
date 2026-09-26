"""Build the JSON body for Brain ``POST /v1/turn`` from transcript text.

Kickoff shape::

    {session_id, turn_id, speaker_role, text, created_at?}

``created_at`` is omitted unless the caller passes it. Audio is not a field.
Voice does not import Brain.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

REQUIRED_TURN_FIELDS: tuple[str, ...] = ("session_id", "turn_id", "speaker_role", "text")
OPTIONAL_TURN_FIELDS: tuple[str, ...] = ("created_at",)
_ALLOWED_TURN_FIELDS = frozenset(REQUIRED_TURN_FIELDS + OPTIONAL_TURN_FIELDS)


def now_iso() -> str:
    """ISO-8601 UTC timestamp with a Z suffix, matching Brain's clock format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_session_id() -> str:
    """A new session id. Reuse the returned value across turns in one conversation."""
    return f"sess-{uuid.uuid4().hex}"


def new_turn_id() -> str:
    """A new unique turn id. Call once per emitted turn."""
    return f"turn-{uuid.uuid4().hex}"


def build_turn(
    text: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    speaker_role: str = "user",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Return a ``POST /v1/turn`` body from ASR (or plain) text.

    ``session_id`` and ``turn_id`` are generated when omitted. ``speaker_role``
    is a free string. Pass ``created_at`` to include that optional field.
    This is the text-first path: no microphone and no ASR call.
    """
    payload: dict[str, Any] = {
        "session_id": _token(session_id, field="session_id", generated=new_session_id),
        "turn_id": _token(turn_id, field="turn_id", generated=new_turn_id),
        "speaker_role": _required_text(speaker_role, field="speaker_role"),
        "text": _required_text(text, field="text"),
    }
    if created_at is not None:
        payload["created_at"] = _required_text(created_at, field="created_at")
    validate_turn_payload(payload)
    return payload


def validate_turn_payload(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` when ``payload`` is not the kickoff turn body."""
    if not isinstance(payload, dict):
        raise ValueError("turn payload must be an object")
    unknown = [key for key in payload if key not in _ALLOWED_TURN_FIELDS]
    if unknown:
        raise ValueError(f"unexpected fields: {', '.join(sorted(unknown))}")
    missing = [key for key in REQUIRED_TURN_FIELDS if _blank(payload.get(key))]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    for key in REQUIRED_TURN_FIELDS + OPTIONAL_TURN_FIELDS:
        if key not in payload:
            continue
        if not isinstance(payload[key], str):
            raise ValueError(f"{key} must be a string")
        if not payload[key].strip():
            raise ValueError(f"{key} is required")


def _token(value: str | None, *, field: str, generated: Callable[[], str]) -> str:
    if value is None:
        return generated()
    return _required_text(value, field=field)


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _blank(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False
