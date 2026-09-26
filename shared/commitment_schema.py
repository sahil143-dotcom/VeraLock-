"""Brain ↔ Vault commitment contract v1.

Field names and status enum MUST match Brain exactly. Vault may ADD columns
for evidence/followup; never rename Brain fields.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class CommitmentStatus(str, Enum):
    """Status enum as UPPER_SNAKE strings (Brain contract)."""

    NO_COMMITMENT = "NO_COMMITMENT"
    DETECTED = "DETECTED"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    CONFIRMED = "CONFIRMED"
    UNRESOLVED_AMBIGUOUS = "UNRESOLVED_AMBIGUOUS"
    SUPERSEDED = "SUPERSEDED"
    WITHDRAWN = "WITHDRAWN"


class ConditionStatus(str, Enum):
    """Vault-only condition status for conditional commitments."""

    PENDING = "PENDING"
    MET = "MET"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class Commitment:
    """Brain commitment fields + optional Vault-only extras.

    Brain fields (never rename):
      commitment_id, topic_id, status, speaker_role, canonical_text, raw_span,
      source_turn_ids, clarification_count, intervened, is_acknowledgement,
      is_intention_only, confidence, conditions, created_at, updated_at, session_id

    Vault-only extras (allowed):
      condition, dependency_owner, condition_status, due_at, next_followup_at,
      superseded_by_commitment_id
    """

    # --- Brain fields ---
    commitment_id: str
    topic_id: str
    status: CommitmentStatus
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

    # --- Vault-only extras ---
    condition: Optional[str] = None
    dependency_owner: Optional[str] = None
    condition_status: Optional[ConditionStatus] = None
    due_at: Optional[str] = None
    next_followup_at: Optional[str] = None
    superseded_by_commitment_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = (
            self.status.value if isinstance(self.status, CommitmentStatus) else self.status
        )
        if self.condition_status is not None:
            data["condition_status"] = (
                self.condition_status.value
                if isinstance(self.condition_status, ConditionStatus)
                else self.condition_status
            )
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Commitment":
        payload = dict(data)
        status = payload.get("status")
        if isinstance(status, str):
            payload["status"] = CommitmentStatus(status)
        cs = payload.get("condition_status")
        if isinstance(cs, str):
            payload["condition_status"] = ConditionStatus(cs)
        # Ignore unknown keys so Brain payloads stay forward-compatible.
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in payload.items() if k in known}
        return cls(**filtered)
