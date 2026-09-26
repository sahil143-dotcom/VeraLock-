"""Brain turn pipeline.

Order: cost gate → rolling context → one A→G reasoner call → guardrails →
clarification cap → commitment state machine. Speech is only SILENT or CLARIFY.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from shared.commitment_schema import Commitment, CommitmentStatus
from shared.persist_handoff import NullPersistPort, PersistHandoff, PersistPort, build_handoff

from intelligence.clarification import ClarificationLedger
from intelligence.clock import now_iso
from intelligence.context import ContextWindow, Turn, as_status
from intelligence.guardrails import decide
from intelligence.models import SILENT, PolicyDecision, ReasonerOutput, TurnInput, TurnResult
from intelligence.reasoner import Reasoner
from intelligence.turn_filter import gate
from state.commitment_machine import CommitmentMachine


class BrainPipeline:
    def __init__(
        self,
        *,
        fixture_mode: bool = True,
        llm: object | None = None,
        persist: PersistPort | None = None,
    ) -> None:
        self.reasoner = Reasoner(llm=llm, fixture_mode=fixture_mode)  # type: ignore[arg-type]
        self.machine = CommitmentMachine()
        self.clarifications = ClarificationLedger()
        self.persist: PersistPort = persist if persist is not None else NullPersistPort()
        self._contexts: dict[str, ContextWindow] = {}

    def context_for(self, session_id: str) -> ContextWindow:
        window = self._contexts.get(session_id)
        if window is None:
            window = ContextWindow(session_id=session_id)
            self._contexts[session_id] = window
        return window

    def handle_turn(self, incoming: TurnInput) -> TurnResult:
        window = self.context_for(incoming.session_id)
        previous = window.turns()[-1] if window.turns() else None
        decision = gate(
            incoming.text,
            previous_text=previous.text if previous else None,
            previous_speaker=previous.speaker_role if previous else None,
            speaker_role=incoming.speaker_role,
        )
        window.add_turn(
            Turn(
                turn_id=incoming.turn_id,
                speaker_role=incoming.speaker_role,
                text=incoming.text,
                created_at=incoming.created_at,
            )
        )
        for commitment in incoming.active_commitments:
            window.upsert(commitment)
            self.clarifications.observe(
                incoming.session_id, commitment.topic_id, commitment.clarification_count
            )

        if not decision.proceed:
            result = TurnResult(
                speech_action=SILENT,
                commitment=None,
                clarification_question=None,
                skipped_by_filter=True,
                filter_reason=decision.reason,
                policy_notes=["filter_skip"],
                reasoning=None,
            )
            self._emit(incoming, result, previous_status=None)
            return result

        snapshot = window.snapshot()
        proposal = self.reasoner.reason(
            snapshot,
            text=decision.text,
            turn_id=incoming.turn_id,
            speaker_role=incoming.speaker_role,
            created_at=incoming.created_at,
        )
        existing = self._active_match(window, proposal.topic_id, incoming.speaker_role)
        intervened = bool(incoming.intervened or (existing.intervened if existing else False))
        clarification_count = self.clarifications.count(incoming.session_id, proposal.topic_id)
        if existing is not None:
            clarification_count = max(clarification_count, existing.clarification_count)

        policy = decide(
            proposal,
            existing=existing,
            clarification_count=clarification_count,
            intervened=intervened,
        )
        policy = self._enforce_cap(policy, proposal, incoming.session_id)

        commitment, previous_status = self._apply(
            incoming, proposal, policy, existing, window
        )
        if commitment.status != CommitmentStatus.NO_COMMITMENT:
            window.upsert(commitment)

        result = TurnResult(
            speech_action=policy.speech_action,
            commitment=commitment,
            clarification_question=policy.clarification_question,
            skipped_by_filter=False,
            filter_reason=decision.reason,
            policy_notes=list(policy.notes),
            reasoning=proposal,
        )
        self._emit(incoming, result, previous_status=previous_status)
        return result

    def _emit(
        self,
        incoming: TurnInput,
        result: TurnResult,
        *,
        previous_status: Optional[str],
    ) -> None:
        """Side effect only. The returned TurnResult is unchanged.

        Calls `shared.persist_handoff.build_handoff`. `session_id` is required.
        A commitment must carry a non-empty `source_turn_ids` that includes
        this turn, and `transition.to_status` equals `commitment.status`.
        """
        session_id = _required_text(incoming.session_id, "session_id")
        turn_id = str(incoming.turn_id)
        if result.skipped_by_filter or result.commitment is None:
            commitment = None
            from_status = None
            to_status = CommitmentStatus.NO_COMMITMENT.value
        else:
            commitment = dict(result.commitment.to_dict())
            to_status = _enforce_commitment(commitment, turn_id)
            from_status = _status_name(previous_status)
        if commitment is not None and to_status != _status_name(commitment.get("status")):
            raise ValueError("transition.to_status must equal commitment.status")

        handoff: PersistHandoff = build_handoff(
            session_id=session_id,
            turn_id=turn_id,
            speaker_role=incoming.speaker_role,
            turn_text=incoming.text,
            created_at=incoming.created_at,
            speech_action=str(result.speech_action),
            from_status=from_status,
            to_status=to_status,
            clarification_question=result.clarification_question,
            skipped_by_filter=bool(result.skipped_by_filter),
            policy_notes=list(result.policy_notes),
            commitment=commitment,
            intervened_this_turn=bool(incoming.intervened),
            conversation_id=_optional_text(incoming.conversation_id),
            intervention_reason=_optional_text(incoming.intervention_reason),
        )
        self.persist.persist(handoff)

    def _enforce_cap(
        self,
        policy: PolicyDecision,
        proposal: ReasonerOutput,
        session_id: str,
    ) -> PolicyDecision:
        """Clarification module is the backstop if a CLARIFY slipped past the count."""
        if policy.speech_action != "CLARIFY":
            return policy
        if self.clarifications.allow(session_id, proposal.topic_id):
            question = policy.clarification_question or proposal.canonical_text
            self.clarifications.record(session_id, proposal.topic_id, question)
            policy.clarification_question = question
            return policy
        policy.status = CommitmentStatus.UNRESOLVED_AMBIGUOUS
        policy.speech_action = SILENT
        policy.clarification_question = None
        policy.apply_to_existing = True
        policy.notes = [*policy.notes, "clarification_cap"]
        return policy

    def _active_match(
        self,
        window: ContextWindow,
        topic_id: str,
        speaker_role: str,
    ) -> Optional[Commitment]:
        rows = [
            c
            for c in window.active_commitments()
            if c.topic_id == topic_id and c.speaker_role == speaker_role
        ]
        if not rows:
            return None
        preference = {
            CommitmentStatus.AWAITING_CLARIFICATION: 0,
            CommitmentStatus.DETECTED: 1,
            CommitmentStatus.CONFIRMED: 2,
        }
        rows.sort(
            key=lambda c: (
                preference.get(as_status(c.status), 9),
                c.updated_at,
            )
        )
        return rows[0]

    def _choose_existing(
        self,
        window: ContextWindow,
        topic_id: str,
        speaker_role: str,
        new_status: CommitmentStatus,
    ) -> Optional[Commitment]:
        viable: list[Commitment] = []
        for commitment in window.for_topic(topic_id, speaker_role):
            current = as_status(commitment.status)
            if current == new_status or self.machine.allowed(current, new_status):
                viable.append(commitment)
        if not viable:
            return None
        open_rows = [c for c in viable if as_status(c.status) in {
            CommitmentStatus.DETECTED,
            CommitmentStatus.AWAITING_CLARIFICATION,
            CommitmentStatus.CONFIRMED,
        }]
        pool = open_rows or viable
        pool.sort(key=lambda c: (c.updated_at, c.commitment_id))
        return pool[-1]

    def _apply(
        self,
        incoming: TurnInput,
        proposal: ReasonerOutput,
        policy: PolicyDecision,
        existing: Optional[Commitment],
        window: ContextWindow,
    ) -> tuple[Commitment, Optional[str]]:
        """Apply policy. The second value is the status before apply, or null on create."""
        status = as_status(policy.status)
        target: Optional[Commitment] = None
        if policy.apply_to_existing:
            # Prefer the active row the reasoner was shown. Fall back to an
            # older row on this topic (for example UNRESOLVED_AMBIGUOUS) when
            # the active set no longer contains it.
            if existing is not None and (
                as_status(existing.status) == status
                or self.machine.allowed(as_status(existing.status), status)
            ):
                target = existing
            else:
                target = self._choose_existing(
                    window, proposal.topic_id, incoming.speaker_role, status
                )

        source_ids = [incoming.turn_id]
        if target is not None:
            source_ids = list(target.source_turn_ids)
            if incoming.turn_id not in source_ids:
                source_ids.append(incoming.turn_id)

        clarification_count = self.clarifications.count(incoming.session_id, proposal.topic_id)
        if target is not None:
            clarification_count = max(clarification_count, target.clarification_count)

        fields = dict(
            canonical_text=proposal.canonical_text or (target.canonical_text if target else ""),
            raw_span=proposal.raw_span,
            source_turn_ids=source_ids,
            clarification_count=clarification_count,
            intervened=policy.intervened,
            is_acknowledgement=policy.is_acknowledgement,
            is_intention_only=policy.is_intention_only,
            confidence=proposal.confidence,
            conditions=proposal.conditions,
        )

        if target is None:
            created = Commitment(
                commitment_id=str(uuid.uuid4()),
                topic_id=proposal.topic_id,
                status=status,
                speaker_role=incoming.speaker_role,
                canonical_text=fields["canonical_text"],
                raw_span=fields["raw_span"],
                source_turn_ids=source_ids,
                clarification_count=clarification_count,
                intervened=policy.intervened,
                is_acknowledgement=policy.is_acknowledgement,
                is_intention_only=policy.is_intention_only,
                confidence=proposal.confidence,
                conditions=proposal.conditions,
                created_at=incoming.created_at or now_iso(),
                updated_at=incoming.created_at or now_iso(),
                session_id=incoming.session_id,
            )
            return self.machine.create(created), None

        # Keep the canonical text of an open commitment when the new turn is a
        # short restatement that still binds; prefer the clearer proposal text
        # when we are confirming or first recording.
        if status == CommitmentStatus.UNRESOLVED_AMBIGUOUS and target.canonical_text:
            fields["canonical_text"] = target.canonical_text
        from_status = as_status(target.status).value
        return (
            self.machine.transition(
                target,
                status,
                updated_at=incoming.created_at or now_iso(),
                **fields,
            ),
            from_status,
        )


def _enforce_commitment(commitment: dict[str, Any], turn_id: str) -> str:
    """Return the commitment status. Reject a row Vault cannot store."""
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
