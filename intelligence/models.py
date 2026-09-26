"""In-memory Brain types. Commitment records themselves come from shared schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from shared.commitment_schema import Commitment

SpeechAction = Literal["SILENT", "CLARIFY"]
Surface = Literal["acknowledgement", "intention", "commitment", "ambiguous", "unrelated"]

SILENT: SpeechAction = "SILENT"
CLARIFY: SpeechAction = "CLARIFY"


@dataclass
class TurnInput:
    """One text turn. Voice audio never enters Brain."""

    session_id: str
    turn_id: str
    speaker_role: str
    text: str
    created_at: str
    intervened: bool = False
    active_commitments: list[Commitment] = field(default_factory=list)
    conversation_id: Optional[str] = None
    intervention_reason: Optional[str] = None


@dataclass(frozen=True)
class FilterDecision:
    """Cost/latency gate result. No commitment status lives here."""

    proceed: bool
    reason: str
    text: str
    original_chars: int


@dataclass
class ReasonerOutput:
    """Parsed single-shot A→G trace."""

    actor: str
    topic_id: str
    matched_commitment_id: Optional[str]
    surface: Surface
    canonical_text: str
    raw_span: Optional[str]
    confidence: float
    conditions: Optional[dict[str, Any]]
    source_turn_ids: list[str]
    is_acknowledgement: bool
    is_intention_only: bool
    proposed_status: str
    suggested_speech: SpeechAction
    clarification_question: Optional[str]
    ag: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "topic_id": self.topic_id,
            "matched_commitment_id": self.matched_commitment_id,
            "surface": self.surface,
            "canonical_text": self.canonical_text,
            "raw_span": self.raw_span,
            "confidence": self.confidence,
            "conditions": self.conditions,
            "source_turn_ids": list(self.source_turn_ids),
            "is_acknowledgement": self.is_acknowledgement,
            "is_intention_only": self.is_intention_only,
            "proposed_status": self.proposed_status,
            "suggested_speech": self.suggested_speech,
            "clarification_question": self.clarification_question,
            "ag": self.ag,
        }


@dataclass
class PolicyDecision:
    """Deterministic guardrail outcome. This, not the model, sets status and speech."""

    status: Any
    speech_action: SpeechAction
    is_acknowledgement: bool
    is_intention_only: bool
    intervened: bool
    clarification_question: Optional[str]
    apply_to_existing: bool
    notes: list[str]


@dataclass
class TurnResult:
    speech_action: SpeechAction
    commitment: Optional[Commitment]
    clarification_question: Optional[str]
    skipped_by_filter: bool
    filter_reason: str
    policy_notes: list[str]
    reasoning: Optional[ReasonerOutput] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "speech_action": self.speech_action,
            "clarification_question": self.clarification_question,
            "skipped_by_filter": self.skipped_by_filter,
            "filter_reason": self.filter_reason,
            "policy_notes": list(self.policy_notes),
            "commitment": self.commitment.to_dict() if self.commitment else None,
            "reasoning": self.reasoning.to_dict() if self.reasoning else None,
        }
