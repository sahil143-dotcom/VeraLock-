"""Row models for Vault SQLite tables (dataclass / TypedDict friendly)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


def _parse_json_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        if not raw.strip():
            return []
        data = json.loads(raw)
        return [str(x) for x in data]
    raise TypeError(f"expected JSON list, got {type(raw)!r}")


def _parse_json_obj(raw: Any) -> Optional[dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return None
        data = json.loads(raw)
        if data is None:
            return None
        if not isinstance(data, dict):
            raise TypeError(f"expected JSON object, got {type(data)!r}")
        return data
    raise TypeError(f"expected JSON object, got {type(raw)!r}")


@dataclass
class ConversationRow:
    conversation_id: str
    session_id: Optional[str]
    title: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "ConversationRow":
        return cls(
            conversation_id=row["conversation_id"],
            session_id=row["session_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class TurnRow:
    turn_id: str
    conversation_id: str
    speaker_role: str
    content: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "TurnRow":
        return cls(
            turn_id=row["turn_id"],
            conversation_id=row["conversation_id"],
            speaker_role=row["speaker_role"],
            content=row["content"],
            created_at=row["created_at"],
        )


@dataclass
class CommitmentRow:
    """Brain fields + Vault-only extras as persisted."""

    commitment_id: str
    topic_id: str
    status: str
    speaker_role: str
    canonical_text: str
    raw_span: Optional[str]
    source_turn_ids: list[str]
    clarification_count: int
    intervened: bool
    is_acknowledgement: bool
    is_intention_only: bool
    confidence: float
    conditions: Optional[dict[str, Any]]
    created_at: str
    updated_at: str
    session_id: Optional[str]
    conversation_id: Optional[str] = None
    condition: Optional[str] = None
    dependency_owner: Optional[str] = None
    condition_status: Optional[str] = None
    due_at: Optional[str] = None
    next_followup_at: Optional[str] = None
    superseded_by_commitment_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: Any) -> "CommitmentRow":
        keys = row.keys() if hasattr(row, "keys") else []
        return cls(
            commitment_id=row["commitment_id"],
            topic_id=row["topic_id"],
            status=row["status"],
            speaker_role=row["speaker_role"],
            canonical_text=row["canonical_text"],
            raw_span=row["raw_span"],
            source_turn_ids=_parse_json_list(row["source_turn_ids"]),
            clarification_count=int(row["clarification_count"]),
            intervened=bool(row["intervened"]),
            is_acknowledgement=bool(row["is_acknowledgement"]),
            is_intention_only=bool(row["is_intention_only"]),
            confidence=float(row["confidence"]),
            conditions=_parse_json_obj(row["conditions"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            session_id=row["session_id"],
            conversation_id=row["conversation_id"] if "conversation_id" in keys else None,
            condition=row["condition"] if "condition" in keys else None,
            dependency_owner=row["dependency_owner"] if "dependency_owner" in keys else None,
            condition_status=row["condition_status"] if "condition_status" in keys else None,
            due_at=row["due_at"] if "due_at" in keys else None,
            next_followup_at=row["next_followup_at"] if "next_followup_at" in keys else None,
            superseded_by_commitment_id=(
                row["superseded_by_commitment_id"]
                if "superseded_by_commitment_id" in keys
                else None
            ),
        )


@dataclass
class CommitmentEventRow:
    event_id: str
    commitment_id: str
    from_status: Optional[str]
    to_status: str
    source_turn_ids: list[str]
    note: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "CommitmentEventRow":
        return cls(
            event_id=row["event_id"],
            commitment_id=row["commitment_id"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            source_turn_ids=_parse_json_list(row["source_turn_ids"]),
            note=row["note"],
            created_at=row["created_at"],
        )


@dataclass
class InterventionEventRow:
    event_id: str
    commitment_id: str
    source_turn_ids: list[str]
    reason: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "InterventionEventRow":
        return cls(
            event_id=row["event_id"],
            commitment_id=row["commitment_id"],
            source_turn_ids=_parse_json_list(row["source_turn_ids"]),
            reason=row["reason"],
            created_at=row["created_at"],
        )
