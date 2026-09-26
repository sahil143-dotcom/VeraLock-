"""Map a Brain turn onto the shared persist handoff.

The payload types live in `shared/persist_handoff.py` (Vault's contract).
This module does not import storage/, evidence/, or followup/.
"""

from __future__ import annotations

from typing import Any, Optional

from shared.commitment_schema import CommitmentStatus
from shared.persist_handoff import PersistHandoff, PersistPort, build_handoff as build_shared_handoff

from intelligence.models import TurnInput, TurnResult

_NO_COMMITMENT = CommitmentStatus.NO_COMMITMENT.value


class RecordingPersistSink(PersistPort):
    """Test double. Keeps handoffs in emit order."""

    def __init__(self) -> None:
        self.handoffs: list[PersistHandoff] = []

    def persist(self, handoff: PersistHandoff) -> None:
        self.handoffs.append(handoff)


def build_handoff(
    turn_input: TurnInput,
    turn_result: TurnResult,
    *,
    previous_status: CommitmentStatus | str | None = None,
    conversation_id: str | None = None,
    intervention_reason: str | None = None,
) -> PersistHandoff:
    """Build one Vault handoff from a finished turn.

    Freeze rules:
    - `session_id` is required.
    - A non-null commitment has a non-empty `source_turn_ids` that includes
      this `turn_id`.
    - When a commitment is present, `transition.to_status` equals
      `commitment["status"]`.
    - `intervention_reason` is optional.

    `previous_status` is the status before apply when this turn updated an
    existing row, and null on create. Filter skips carry no commitment;
    `to_status` is `NO_COMMITMENT` so the shared Transition stays well-typed.
    """
    session_id = _required_text(turn_input.session_id, "session_id")
    turn_id = str(turn_input.turn_id)

    commitment: Optional[dict[str, Any]]
    if turn_result.skipped_by_filter or turn_result.commitment is None:
        commitment = None
        from_status = None
        to_status = _NO_COMMITMENT
    else:
        commitment = dict(turn_result.commitment.to_dict())
        to_status = _enforce_commitment(commitment, turn_id)
        from_status = _status_name(previous_status)

    if commitment is not None and to_status != _status_name(commitment.get("status")):
        raise ValueError("transition.to_status must equal commitment.status")

    resolved_conversation = conversation_id
    if resolved_conversation is None:
        resolved_conversation = turn_input.conversation_id
    resolved_reason = intervention_reason
    if resolved_reason is None:
        resolved_reason = turn_input.intervention_reason

    return build_shared_handoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role=turn_input.speaker_role,
        turn_text=turn_input.text,
        created_at=turn_input.created_at,
        speech_action=str(turn_result.speech_action),
        from_status=from_status,
        to_status=to_status,
        clarification_question=turn_result.clarification_question,
        skipped_by_filter=bool(turn_result.skipped_by_filter),
        policy_notes=list(turn_result.policy_notes),
        commitment=commitment,
        intervened_this_turn=bool(turn_input.intervened),
        conversation_id=_optional_text(resolved_conversation),
        intervention_reason=_optional_text(resolved_reason),
    )


def _enforce_commitment(commitment: dict[str, Any], turn_id: str) -> str:
    """Return commitment status and reject a payload Vault cannot store."""
    status = _status_name(commitment.get("status"))
    if not status:
        raise ValueError("commitment.status is required")
    commitment["status"] = status

    raw_ids = commitment.get("source_turn_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("commitment.source_turn_ids must be non-empty")
    source_turn_ids = [str(item) for item in raw_ids]
    if not turn_id or turn_id not in source_turn_ids:
        raise ValueError("commitment.source_turn_ids must include turn_id")
    commitment["source_turn_ids"] = source_turn_ids
    return status


def _required_text(value: Any, field_name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _status_name(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, CommitmentStatus):
        return value.value
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        text = enum_value.strip()
        return text or None
    text = str(value).strip()
    return text or None
