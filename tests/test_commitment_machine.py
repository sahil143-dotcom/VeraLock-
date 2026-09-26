"""Commitment graph: illegal hops raise; legal hops return a new record."""

from __future__ import annotations

import pytest

from shared.commitment_schema import Commitment, CommitmentStatus
from state.commitment_machine import CommitmentMachine, IllegalTransitionError


def make(status: CommitmentStatus, **overrides) -> Commitment:
    now = "2026-09-26T00:00:00Z"
    base = dict(
        commitment_id="cmt-1",
        topic_id="proposal-delivery",
        status=status,
        speaker_role="user",
        canonical_text="Send the proposal by Friday.",
        raw_span="I will send the proposal by Friday.",
        source_turn_ids=["turn-0"],
        clarification_count=0,
        intervened=False,
        is_acknowledgement=False,
        is_intention_only=False,
        confidence=0.9,
        conditions=None,
        created_at=now,
        updated_at=now,
        session_id="sess-1",
    )
    base.update(overrides)
    return Commitment(**base)


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (CommitmentStatus.CONFIRMED, CommitmentStatus.DETECTED),
        (CommitmentStatus.CONFIRMED, CommitmentStatus.AWAITING_CLARIFICATION),
        (CommitmentStatus.NO_COMMITMENT, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.UNRESOLVED_AMBIGUOUS, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.UNRESOLVED_AMBIGUOUS, CommitmentStatus.AWAITING_CLARIFICATION),
        (CommitmentStatus.WITHDRAWN, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.WITHDRAWN, CommitmentStatus.AWAITING_CLARIFICATION),
        (CommitmentStatus.SUPERSEDED, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.AWAITING_CLARIFICATION, CommitmentStatus.DETECTED),
    ],
)
def test_illegal_transitions_rejected(src: CommitmentStatus, dst: CommitmentStatus):
    machine = CommitmentMachine()
    with pytest.raises(IllegalTransitionError) as caught:
        machine.transition(
            make(src),
            dst,
            updated_at="2026-09-26T00:05:00Z",
            source_turn_ids=["turn-0", "turn-1"],
        )
    assert caught.value.from_status == src
    assert caught.value.to_status == dst


def test_create_rejects_superseded_and_withdrawn():
    machine = CommitmentMachine()
    for status in (CommitmentStatus.SUPERSEDED, CommitmentStatus.WITHDRAWN):
        with pytest.raises(IllegalTransitionError) as caught:
            machine.create(make(status))
        assert caught.value.from_status is None
        assert caught.value.to_status == status


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (CommitmentStatus.DETECTED, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.DETECTED, CommitmentStatus.AWAITING_CLARIFICATION),
        (CommitmentStatus.AWAITING_CLARIFICATION, CommitmentStatus.CONFIRMED),
        (CommitmentStatus.AWAITING_CLARIFICATION, CommitmentStatus.UNRESOLVED_AMBIGUOUS),
        (CommitmentStatus.CONFIRMED, CommitmentStatus.WITHDRAWN),
        (CommitmentStatus.CONFIRMED, CommitmentStatus.SUPERSEDED),
        (CommitmentStatus.DETECTED, CommitmentStatus.NO_COMMITMENT),
    ],
)
def test_legal_transitions(src: CommitmentStatus, dst: CommitmentStatus):
    machine = CommitmentMachine()
    updated = machine.transition(
        make(src),
        dst,
        updated_at="2026-09-26T00:05:00Z",
        source_turn_ids=["turn-0", "turn-9"],
    )
    assert updated.status == dst
    assert updated.commitment_id == "cmt-1"
    assert updated.source_turn_ids == ["turn-0", "turn-9"]
    assert updated.updated_at == "2026-09-26T00:05:00Z"
    assert updated.created_at == "2026-09-26T00:00:00Z"


def test_same_status_update_is_not_an_illegal_transition():
    machine = CommitmentMachine()
    updated = machine.transition(
        make(CommitmentStatus.DETECTED),
        CommitmentStatus.DETECTED,
        updated_at="2026-09-26T00:06:00Z",
        source_turn_ids=["turn-0", "turn-2"],
        intervened=True,
    )
    assert updated.status == CommitmentStatus.DETECTED
    assert updated.intervened is True
    assert updated.source_turn_ids[-1] == "turn-2"
