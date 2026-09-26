"""Map a Brain turn onto Vault's persist handoff.

Imports the canonical types from `shared.persist_handoff`. Does not define a
second payload, and does not import storage/, evidence/, or followup/.
"""

from __future__ import annotations

from typing import Any, Optional

from shared.commitment_schema import CommitmentStatus
from shared.persist_handoff import (
    NullPersistPort,
    PersistHandoff,
    PersistPort,
    Transition,
    build_handoff,
)

from intelligence.models import TurnInput, TurnResult

_NO_COMMITMENT = CommitmentStatus.NO_COMMITMENT.value

# Old name. Same object as Vault's no-op port.
NullPersistSink = NullPersistPort


class RecordingPersistSink(PersistPort):
    """Test double. Keeps handoffs in emit order."""

    def __init__(self) -> None:
        self.handoffs: list[PersistHandoff] = []

    def persist(self, handoff: PersistHandoff) -> None:
        self.handoffs.append(handoff)


def from_turn(
    turn_input: TurnInput,
    turn_result: TurnResult,
    *,
    previous_status: CommitmentStatus | str | None = None,
    conversation_id: str | None = None,
    intervention_reason: str | None = None,
) -> PersistHandoff:
    """Build one handoff by calling shared `build_handoff`.

    Freeze rules:
    - `session_id` is required.
    - A non-null commitment has a non-empty `source_turn_ids` that includes
      this `turn_id`.
    - When a commitment is present, `transition.to_status` equals
      `commitment["status"]`. `to_status` is always a string.
    - Filter skips use `commitment=None`, `from_status=None`, and
      `to_status="NO_COMMITMENT"`.
    - An acknowledgement may carry a commitment dict with status
      `NO_COMMITMENT`; `to_status` is then `"NO_COMMITMENT"`.
    - `intervention_reason` is set only when this turn intervened.
    """
    session_id = _required_text(turn_input.session_id, "session_id")
    turn_id = str(turn_input.turn_id)
    intervened = bool(turn_input.intervened)

    if turn_result.skipped_by_filter or turn_result.commitment is None:
        commitment = None
        transition = Transition(from_status=None, to_status=_NO_COMMITMENT)
    else:
        commitment = dict(turn_result.commitment.to_dict())
        to_status = _enforce_commitment(commitment, turn_id)
        transition = Transition(
            from_status=_status_name(previous_status),
            to_status=to_status,
        )
        if transition.to_status != commitment["status"]:
            raise ValueError("transition.to_status must equal commitment.status")

    if not isinstance(transition.to_status, str) or not transition.to_status:
        raise ValueError("transition.to_status is required")

    resolved_conversation = conversation_id
    if resolved_conversation is None:
        resolved_conversation = turn_input.conversation_id
    resolved_reason = intervention_reason
    if resolved_reason is None:
        resolved_reason = turn_input.intervention_reason
    if not intervened:
        resolved_reason = None

    return build_handoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role=turn_input.speaker_role,
        turn_text=turn_input.text,
        created_at=turn_input.created_at,
        speech_action=str(turn_result.speech_action),
        from_status=transition.from_status,
        to_status=transition.to_status,
        clarification_question=turn_result.clarification_question,
        skipped_by_filter=bool(turn_result.skipped_by_filter),
        policy_notes=list(turn_result.policy_notes),
        commitment=commitment,
        intervened_this_turn=intervened,
        conversation_id=_optional_text(resolved_conversation),
        intervention_reason=_optional_text(resolved_reason),
    )


def _enforce_commitment(commitment: dict[str, Any], turn_id: str) -> str:
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
