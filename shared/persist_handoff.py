"""Brain → Vault persist handoff contract.

Brain emits PersistHandoff after TurnResult. Vault implements PersistPort.
This module stays free of intelligence/ and storage/ imports so either side
can depend on it without cycles.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class Transition:
    """Status transition recorded on this turn."""

    from_status: Optional[str]
    to_status: str

    def to_dict(self) -> dict[str, Any]:
        return {"from_status": self.from_status, "to_status": self.to_status}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Transition":
        return cls(
            from_status=data.get("from_status"),
            to_status=str(data["to_status"]),
        )


@dataclass
class PersistHandoff:
    """Frozen Brain→Vault payload for one turn.

    commitment is the Brain contract v1 dict from Commitment.to_dict(), or null
    when the turn produced no commitment row change.
    """

    session_id: str
    turn_id: str
    speaker_role: str
    turn_text: str
    created_at: str  # ISO-8601 UTC
    speech_action: str  # SILENT | CLARIFY
    clarification_question: Optional[str]
    skipped_by_filter: bool
    policy_notes: list[str]
    commitment: Optional[dict[str, Any]]
    transition: Transition
    intervened_this_turn: bool
    conversation_id: Optional[str] = None
    intervention_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["transition"] = self.transition.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PersistHandoff":
        payload = dict(data)
        raw_transition = payload.get("transition") or {}
        if isinstance(raw_transition, Transition):
            transition = raw_transition
        else:
            transition = Transition.from_dict(dict(raw_transition))
        notes = payload.get("policy_notes") or []
        return cls(
            session_id=str(payload["session_id"]),
            turn_id=str(payload["turn_id"]),
            speaker_role=str(payload["speaker_role"]),
            turn_text=str(payload["turn_text"]),
            created_at=str(payload["created_at"]),
            speech_action=str(payload["speech_action"]),
            clarification_question=payload.get("clarification_question"),
            skipped_by_filter=bool(payload.get("skipped_by_filter", False)),
            policy_notes=[str(n) for n in notes],
            commitment=payload.get("commitment"),
            transition=transition,
            intervened_this_turn=bool(payload.get("intervened_this_turn", False)),
            conversation_id=payload.get("conversation_id"),
            intervention_reason=payload.get("intervention_reason"),
        )


class PersistPort(ABC):
    """Vault sink interface. Brain holds a PersistPort (default NullPersistPort)."""

    @abstractmethod
    def persist(self, handoff: PersistHandoff) -> None:
        """Write conversation/turn/commitment/intervention rows for one handoff."""


class NullPersistPort(PersistPort):
    """No-op sink — Brain default until Vault wiring is attached."""

    def persist(self, handoff: PersistHandoff) -> None:  # noqa: ARG002
        return None


# Alias kept for callers that prefer "sink" naming.
NullSink = NullPersistPort


def build_handoff(
    *,
    session_id: str,
    turn_id: str,
    speaker_role: str,
    turn_text: str,
    created_at: str,
    speech_action: str,
    from_status: Optional[str],
    to_status: str,
    clarification_question: Optional[str] = None,
    skipped_by_filter: bool = False,
    policy_notes: Optional[list[str]] = None,
    commitment: Optional[dict[str, Any]] = None,
    intervened_this_turn: bool = False,
    conversation_id: Optional[str] = None,
    intervention_reason: Optional[str] = None,
) -> PersistHandoff:
    """Thin builder from primitives + optional commitment dict.

    Prefer this over importing TurnResult so shared/ stays free of intelligence/.
    Brain can map TurnResult fields into these kwargs at emit time.
    """
    return PersistHandoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role=speaker_role,
        turn_text=turn_text,
        created_at=created_at,
        speech_action=speech_action,
        clarification_question=clarification_question,
        skipped_by_filter=skipped_by_filter,
        policy_notes=list(policy_notes or []),
        commitment=commitment,
        transition=Transition(from_status=from_status, to_status=to_status),
        intervened_this_turn=intervened_this_turn,
        conversation_id=conversation_id,
        intervention_reason=intervention_reason,
    )


__all__ = [
    "Transition",
    "PersistHandoff",
    "PersistPort",
    "NullPersistPort",
    "NullSink",
    "build_handoff",
]
