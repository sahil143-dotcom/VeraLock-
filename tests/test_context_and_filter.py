"""Turn filter and rolling context. The filter never emits a commitment status."""

from __future__ import annotations

from intelligence.context import MAX_TURNS, ContextWindow, Turn
from intelligence.models import SILENT
from intelligence.pipeline import BrainPipeline
from intelligence.turn_filter import gate
from shared.commitment_schema import Commitment, CommitmentStatus
from tests.test_policy import make_commitment, turn


def test_filter_passes_acknowledgement_text():
    decision = gate("ok")
    assert decision.proceed is True
    assert decision.reason == "pass"
    assert not hasattr(decision, "status")


def test_filter_skips_empty_and_filler_without_a_status():
    empty = gate("   ")
    assert empty.proceed is False
    assert empty.reason == "empty"
    filler = gate("uh")
    assert filler.proceed is False
    assert filler.reason == "filler"


def test_filter_truncates_but_still_proceeds():
    decision = gate("I will send the proposal by Friday. " * 500, max_chars=80)
    assert decision.proceed is True
    assert decision.reason == "truncated"
    assert len(decision.text) == 80
    assert decision.original_chars > 80


def test_filter_drops_immediate_duplicate_from_same_speaker():
    decision = gate(
        "Got it.",
        previous_text="got it",
        previous_speaker="user",
        speaker_role="user",
    )
    assert decision.proceed is False
    assert decision.reason == "duplicate"


def test_pipeline_skip_is_silent_and_not_no_commitment():
    pipeline = BrainPipeline()
    result = pipeline.handle_turn(turn("   ", turn_id="blank"))
    assert result.skipped_by_filter is True
    assert result.speech_action == SILENT
    assert result.commitment is None
    assert result.reasoning is None

    filler = pipeline.handle_turn(turn("um", turn_id="filler"))
    assert filler.commitment is None
    assert filler.skipped_by_filter is True


def test_duplicate_ack_is_a_filter_skip_not_a_second_verdict():
    pipeline = BrainPipeline()
    first = pipeline.handle_turn(turn("Got it.", turn_id="t1"))
    assert first.commitment is not None
    assert first.commitment.status == CommitmentStatus.NO_COMMITMENT
    second = pipeline.handle_turn(
        turn("Got it.", turn_id="t2", created_at="2026-09-26T00:01:00Z")
    )
    assert second.skipped_by_filter is True
    assert second.commitment is None
    assert second.filter_reason == "duplicate"


def test_context_keeps_last_six_turns_and_speaker_roles():
    window = ContextWindow(session_id="sess-ctx")
    for index in range(MAX_TURNS + 1):
        role = "user" if index % 2 == 0 else "counterpart"
        window.add_turn(
            Turn(
                turn_id=f"t{index}",
                speaker_role=role,
                text=f"line {index}",
                created_at=f"2026-09-26T00:00:0{index}Z",
            )
        )
    confirmed = make_commitment(CommitmentStatus.CONFIRMED, speaker_role="facilitator")
    window.upsert(confirmed)
    window.upsert(
        make_commitment(
            CommitmentStatus.NO_COMMITMENT,
            commitment_id="cmt-ack",
            topic_id="acknowledgement",
            speaker_role="user",
        )
    )
    snapshot = window.snapshot()
    assert len(snapshot.turns) == MAX_TURNS
    assert snapshot.turns[0].turn_id == "t1"
    assert snapshot.turns[-1].turn_id == "t6"
    assert [c.commitment_id for c in snapshot.active_commitments] == ["cmt-1"]
    assert snapshot.speaker_roles[0] in {"user", "counterpart"}
    assert "facilitator" in snapshot.speaker_roles
    assert "user" in snapshot.speaker_roles
    assert "counterpart" in snapshot.speaker_roles


def test_active_commitments_exclude_terminal_statuses():
    window = ContextWindow(session_id="sess-active")
    for status, cid in (
        (CommitmentStatus.DETECTED, "d"),
        (CommitmentStatus.AWAITING_CLARIFICATION, "a"),
        (CommitmentStatus.CONFIRMED, "c"),
        (CommitmentStatus.UNRESOLVED_AMBIGUOUS, "u"),
        (CommitmentStatus.WITHDRAWN, "w"),
        (CommitmentStatus.NO_COMMITMENT, "n"),
    ):
        window.upsert(
            make_commitment(status, commitment_id=cid, topic_id=cid, updated_at=f"2026-09-26T00:00:0{cid}Z")
        )
    active = {c.commitment_id for c in window.active_commitments()}
    assert active == {"d", "a", "c"}
