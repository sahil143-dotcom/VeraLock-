"""Build the JSON body Brain accepts on ``POST /v1/turn``.

Required keys match ``handle_turn_payload`` / ``TurnIn``:
``session_id``, ``turn_id``, ``speaker_role``, ``text``.

Optional keys: ``created_at``, ``intervened``, ``active_commitments``,
``conversation_id``, ``intervention_reason``.

Commitment dicts are passed through. Voice does not import Brain models and
does not rename commitment fields. Audio is not a turn field.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

REQUIRED_TURN_FIELDS: tuple[str, ...] = ("session_id", "turn_id", "speaker_role", "text")
OPTIONAL_TURN_FIELDS: tuple[str, ...] = (
    "created_at",
    "intervened",
    "active_commitments",
    "conversation_id",
    "intervention_reason",
)
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


def build_turn_payload(
    text: str,
    *,
    session_id: str | None = None,
    turn_id: str | None = None,
    speaker_role: str = "user",
    created_at: str | None = None,
    intervened: bool = False,
    active_commitments: list[dict[str, Any]] | None = None,
    conversation_id: str | None = None,
    intervention_reason: str | None = None,
) -> dict[str, Any]:
    """Return a text turn body. ``text`` is required; no audio key is emitted.

    ``session_id`` and ``turn_id`` are generated when omitted. Pass the same
    ``session_id`` on later turns. ``speaker_role`` is a free string.
    """
    payload: dict[str, Any] = {
        "session_id": _require_token(session_id, field="session_id", generated=new_session_id),
        "turn_id": _require_token(turn_id, field="turn_id", generated=new_turn_id),
        "speaker_role": _require_text(speaker_role, field="speaker_role"),
        "text": _require_text(text, field="text"),
        "created_at": _created_at(created_at),
        "intervened": bool(intervened),
    }
    if active_commitments is not None:
        payload["active_commitments"] = _copy_commitments(active_commitments)
    if conversation_id is not None:
        token = str(conversation_id).strip()
        if token:
            payload["conversation_id"] = token
    if intervention_reason is not None:
        reason = str(intervention_reason).strip()
        if reason:
            payload["intervention_reason"] = reason
    validate_turn_payload(payload)
    return payload


def validate_turn_payload(payload: dict[str, Any]) -> None:
    """Raise ``ValueError`` when ``payload`` is not a Brain turn body."""
    if not isinstance(payload, dict):
        raise ValueError("turn payload must be an object")
    unknown = [key for key in payload if key not in _ALLOWED_TURN_FIELDS]
    if unknown:
        raise ValueError(f"unexpected fields: {', '.join(sorted(unknown))}")
    missing = [key for key in REQUIRED_TURN_FIELDS if _missing_text(payload.get(key))]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    for key in ("session_id", "turn_id", "speaker_role", "text", "created_at"):
        if key not in payload:
            continue
        if not isinstance(payload[key], str):
            raise ValueError(f"{key} must be a string")
        if key == "created_at" and not payload[key].strip():
            raise ValueError("created_at must be a non-empty string")
    if "intervened" in payload and not isinstance(payload["intervened"], bool):
        raise ValueError("intervened must be a bool")
    commitments = payload.get("active_commitments")
    if commitments is not None:
        if not isinstance(commitments, list):
            raise ValueError("active_commitments must be a list")
        for item in commitments:
            if not isinstance(item, dict):
                raise ValueError("active_commitments entries must be objects")
    for key in ("conversation_id", "intervention_reason"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
            raise ValueError(f"{key} must be a non-empty string")


def _created_at(value: str | None) -> str:
    if value is None:
        return now_iso()
    return _require_text(value, field="created_at")


def _missing_text(value: object) -> bool:
    """True when a required string is absent or blank. Wrong types are checked later."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _require_token(value: str | None, *, field: str, generated: Callable[[], str]) -> str:
    if value is None:
        return generated()
    return _require_text(value, field=field)


def _copy_commitments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        raise ValueError("active_commitments must be a list")
    copied: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("active_commitments entries must be objects")
        copied.append(dict(item))
    return copied
