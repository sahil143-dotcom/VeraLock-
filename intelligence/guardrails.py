"""Deterministic policy applied after the reasoner.

The model may propose a status and a speech hint. These rules win:

- Acknowledgement is not a commitment → NO_COMMITMENT, SILENT.
- Intention is not a commitment → NO_COMMITMENT, SILENT.
- intervened=true blocks automatic CONFIRMED. Speech stays SILENT.
- At most one clarification per topic. A still-ambiguous topic becomes
  UNRESOLVED_AMBIGUOUS and SILENT.
- A clear commitment is CONFIRMED and SILENT.
"""

from __future__ import annotations

from shared.commitment_schema import Commitment, CommitmentStatus

from intelligence.clarification import build_question
from intelligence.context import as_status
from intelligence.models import CLARIFY, SILENT, PolicyDecision, ReasonerOutput

_OPEN = frozenset(
    {
        CommitmentStatus.DETECTED,
        CommitmentStatus.AWAITING_CLARIFICATION,
    }
)


def _status(proposal: ReasonerOutput) -> CommitmentStatus:
    return CommitmentStatus(proposal.proposed_status)


def decide(
    proposal: ReasonerOutput,
    *,
    existing: Commitment | None,
    clarification_count: int,
    intervened: bool,
) -> PolicyDecision:
    surface = proposal.surface
    existing_status = as_status(existing.status) if existing is not None else None
    notes: list[str] = []

    if surface == "acknowledgement" or proposal.is_acknowledgement:
        notes.append("ack_not_commitment")
        return PolicyDecision(
            status=CommitmentStatus.NO_COMMITMENT,
            speech_action=SILENT,
            is_acknowledgement=True,
            is_intention_only=False,
            intervened=intervened,
            clarification_question=None,
            apply_to_existing=False,
            notes=notes,
        )

    if surface == "intention" or proposal.is_intention_only:
        notes.append("intention_not_commitment")
        return PolicyDecision(
            status=CommitmentStatus.NO_COMMITMENT,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=True,
            intervened=intervened,
            clarification_question=None,
            apply_to_existing=False,
            notes=notes,
        )

    if surface == "unrelated":
        notes.append("unrelated")
        return PolicyDecision(
            status=CommitmentStatus.NO_COMMITMENT,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=False,
            intervened=intervened,
            clarification_question=None,
            apply_to_existing=False,
            notes=notes,
        )

    if existing_status == CommitmentStatus.CONFIRMED:
        notes.append("already_confirmed")
        return PolicyDecision(
            status=CommitmentStatus.CONFIRMED,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=False,
            intervened=bool(intervened or (existing.intervened if existing else False)),
            clarification_question=None,
            apply_to_existing=True,
            notes=notes,
        )

    wants_confirm = surface == "commitment" or _status(proposal) == CommitmentStatus.CONFIRMED
    if wants_confirm and intervened:
        hold = existing_status if existing_status in _OPEN else CommitmentStatus.DETECTED
        notes.append("blocked_auto_confirm")
        return PolicyDecision(
            status=hold,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=False,
            intervened=True,
            clarification_question=None,
            apply_to_existing=existing is not None and existing_status in _OPEN,
            notes=notes,
        )

    if wants_confirm:
        notes.append("clear_commitment_silent")
        return PolicyDecision(
            status=CommitmentStatus.CONFIRMED,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=False,
            intervened=intervened,
            clarification_question=None,
            apply_to_existing=True,
            notes=notes,
        )

    # Ambiguous (and any other non-confirming residue): one question, then stop.
    if clarification_count >= 1:
        notes.append("clarification_cap")
        return PolicyDecision(
            status=CommitmentStatus.UNRESOLVED_AMBIGUOUS,
            speech_action=SILENT,
            is_acknowledgement=False,
            is_intention_only=False,
            intervened=intervened,
            clarification_question=None,
            apply_to_existing=True,
            notes=notes,
        )

    question = build_question(proposal.topic_id, proposal.canonical_text)
    notes.append("clarify_once")
    return PolicyDecision(
        status=CommitmentStatus.AWAITING_CLARIFICATION,
        speech_action=CLARIFY,
        is_acknowledgement=False,
        is_intention_only=False,
        intervened=intervened,
        clarification_question=question,
        apply_to_existing=True,
        notes=notes,
    )
