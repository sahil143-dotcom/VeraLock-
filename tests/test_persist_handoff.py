"""Brain emit side of the persist handoff. Uses Vault's shared contract.

No API keys. Does not import storage, evidence, or followup.
"""

from __future__ import annotations

import pytest

from intelligence.api import create_app, get_pipeline, handle_turn_payload
from intelligence.models import SILENT, TurnInput, TurnResult
from intelligence.pipeline import BrainPipeline
from shared.commitment_schema import Commitment, CommitmentStatus
from shared.persist_handoff import NullPersistPort, PersistHandoff, PersistPort, build_handoff

BRAIN_FIELDS = [
    "commitment_id",
    "topic_id",
    "status",
    "speaker_role",
    "canonical_text",
    "raw_span",
    "source_turn_ids",
    "clarification_count",
    "intervened",
    "is_acknowledgement",
    "is_intention_only",
    "confidence",
    "conditions",
    "created_at",
    "updated_at",
    "session_id",
]

class RecordingPersistSink(PersistPort):
    """In-test sink. Production wiring uses Vault's PersistPort."""

    def __init__(self) -> None:
        self.handoffs: list[PersistHandoff] = []

    def persist(self, handoff: PersistHandoff) -> None:
        self.handoffs.append(handoff)


def run(incoming: TurnInput) -> tuple[TurnResult, PersistHandoff]:
    sink = RecordingPersistSink()
    result = BrainPipeline(persist=sink).handle_turn(incoming)
    assert len(sink.handoffs) == 1
    return result, sink.handoffs[0]


TURN_RESULT_KEYS = {
    "speech_action",
    "clarification_question",
    "skipped_by_filter",
    "filter_reason",
    "policy_notes",
    "commitment",
    "reasoning",
}


def turn(text: str, **kwargs) -> TurnInput:
    return TurnInput(
        session_id=kwargs.get("session_id", "sess-1"),
        turn_id=kwargs.get("turn_id", "turn-1"),
        speaker_role=kwargs.get("speaker_role", "user"),
        text=text,
        created_at=kwargs.get("created_at", "2026-09-26T00:00:00Z"),
        intervened=kwargs.get("intervened", False),
        active_commitments=kwargs.get("active_commitments") or [],
        conversation_id=kwargs.get("conversation_id"),
        intervention_reason=kwargs.get("intervention_reason"),
    )


def _commitment(**overrides) -> Commitment:
    now = "2026-09-26T00:00:00Z"
    base = dict(
        commitment_id="cmt-1",
        topic_id="proposal-delivery",
        status=CommitmentStatus.DETECTED,
        speaker_role="user",
        canonical_text="Send the proposal by Friday.",
        raw_span="I will send the proposal by Friday.",
        source_turn_ids=["turn-1"],
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


def _result(commitment: Commitment | None) -> TurnResult:
    return TurnResult(
        speech_action=SILENT,
        commitment=commitment,
        clarification_question=None,
        skipped_by_filter=False,
        filter_reason="pass",
        policy_notes=[],
    )


def test_default_pipeline_uses_null_persist_port():
    pipeline = BrainPipeline()
    assert isinstance(pipeline.persist, NullPersistPort)


def test_build_handoff_ack_no_commitment():
    incoming = turn("Got it.")
    result, handoff = run(incoming)

    assert handoff.speech_action == "SILENT"
    assert handoff.skipped_by_filter is False
    assert handoff.commitment is not None
    assert handoff.commitment["status"] == "NO_COMMITMENT"
    assert handoff.commitment["is_acknowledgement"] is True
    assert list(handoff.commitment)[: len(BRAIN_FIELDS)] == BRAIN_FIELDS
    assert incoming.turn_id in handoff.commitment["source_turn_ids"]
    assert handoff.transition.from_status is None
    assert handoff.transition.to_status == "NO_COMMITMENT"
    assert handoff.transition.to_status == handoff.commitment["status"]
    assert handoff.intervened_this_turn is False
    assert handoff.intervention_reason is None
    assert "ack_not_commitment" in handoff.policy_notes


def test_build_handoff_clear_confirmed_create():
    incoming = turn("I will send the proposal by Friday.")
    result, handoff = run(incoming)
    direct = build_handoff(
        session_id=incoming.session_id,
        turn_id=incoming.turn_id,
        speaker_role=incoming.speaker_role,
        turn_text=incoming.text,
        created_at=incoming.created_at,
        speech_action=result.speech_action,
        from_status=None,
        to_status="CONFIRMED",
        clarification_question=result.clarification_question,
        skipped_by_filter=False,
        policy_notes=result.policy_notes,
        commitment=result.commitment.to_dict() if result.commitment else None,
        intervened_this_turn=False,
    )
    assert handoff.to_dict() == direct.to_dict()

    assert handoff.speech_action == "SILENT"
    assert handoff.commitment is not None
    assert handoff.commitment["status"] == "CONFIRMED"
    assert handoff.commitment["topic_id"] == "proposal-delivery"
    assert incoming.turn_id in handoff.commitment["source_turn_ids"]
    assert handoff.transition.from_status is None
    assert handoff.transition.to_status == "CONFIRMED"
    assert handoff.turn_text == incoming.text
    assert handoff.conversation_id is None


def test_build_handoff_clarify():
    incoming = turn("I'll handle the budget review soon.", turn_id="turn-a")
    result, handoff = run(incoming)

    assert handoff.speech_action == "CLARIFY"
    assert handoff.clarification_question
    assert "budget-review" in handoff.clarification_question
    assert handoff.commitment is not None
    assert handoff.commitment["status"] == "AWAITING_CLARIFICATION"
    assert incoming.turn_id in handoff.commitment["source_turn_ids"]
    assert handoff.transition.from_status is None
    assert handoff.transition.to_status == "AWAITING_CLARIFICATION"


def test_build_handoff_filter_skip_has_null_commitment():
    incoming = turn("um", turn_id="turn-filler")
    result, handoff = run(incoming)

    assert result.commitment is None
    assert handoff.skipped_by_filter is True
    assert handoff.speech_action == "SILENT"
    assert handoff.commitment is None
    assert handoff.transition.from_status is None
    assert handoff.transition.to_status == "NO_COMMITMENT"
    assert handoff.policy_notes == ["filter_skip"]
    assert handoff.turn_text == "um"
    assert "filter_reason" not in handoff.to_dict()


def test_build_handoff_intervened_this_turn():
    incoming = turn(
        "I will send the proposal by Friday.",
        intervened=True,
        intervention_reason="user asked to hold",
    )
    result, handoff = run(incoming)

    assert handoff.intervened_this_turn is True
    assert handoff.intervention_reason == "user asked to hold"
    assert handoff.commitment is not None
    assert handoff.commitment["status"] == "DETECTED"
    assert handoff.commitment["intervened"] is True
    assert incoming.turn_id in handoff.commitment["source_turn_ids"]
    assert handoff.transition.from_status is None
    assert handoff.transition.to_status == "DETECTED"
    assert "blocked_auto_confirm" in handoff.policy_notes


def test_session_id_is_required():
    incoming = turn("Got it.", session_id="  ")
    pipeline = BrainPipeline()
    with pytest.raises(ValueError, match="session_id"):
        pipeline._emit(incoming, _result(_commitment()), previous_status=None)


def test_commitment_source_turn_ids_must_include_turn_id():
    incoming = turn("I will send the proposal by Friday.", turn_id="turn-9")
    pipeline = BrainPipeline()
    with pytest.raises(ValueError, match="source_turn_ids"):
        pipeline._emit(incoming, _result(_commitment(source_turn_ids=[])), previous_status=None)

    with pytest.raises(ValueError, match="turn_id"):
        pipeline._emit(
            incoming,
            _result(_commitment(source_turn_ids=["turn-0"])),
            previous_status=None,
        )


def test_commitment_status_matches_transition_to_status():
    incoming = turn("I will send the proposal by Friday.", conversation_id="conv-9")
    result, handoff = run(incoming)
    payload = handoff.to_dict()
    again = PersistHandoff.from_dict(payload)

    assert again.to_dict() == payload
    assert payload["commitment"]["status"] == payload["transition"]["to_status"]
    assert payload["conversation_id"] == "conv-9"
    assert set(BRAIN_FIELDS).issubset(payload["commitment"])


def test_pipeline_emits_exactly_one_handoff_per_turn():
    sink = RecordingPersistSink()
    pipeline = BrainPipeline(persist=sink)

    pipeline.handle_turn(turn("Got it.", turn_id="t1"))
    assert len(sink.handoffs) == 1
    assert sink.handoffs[0].commitment is not None
    assert sink.handoffs[0].transition.to_status == "NO_COMMITMENT"

    pipeline.handle_turn(turn("I will send the proposal by Friday.", turn_id="t2"))
    assert len(sink.handoffs) == 2
    assert sink.handoffs[1].transition.from_status is None
    assert sink.handoffs[1].transition.to_status == "CONFIRMED"
    assert "t2" in sink.handoffs[1].commitment["source_turn_ids"]

    pipeline.handle_turn(turn("um", turn_id="t3"))
    assert len(sink.handoffs) == 3
    assert sink.handoffs[2].skipped_by_filter is True
    assert sink.handoffs[2].commitment is None
    assert sink.handoffs[2].transition.to_status == "NO_COMMITMENT"


def test_pipeline_records_from_status_when_updating_existing():
    sink = RecordingPersistSink()
    pipeline = BrainPipeline(persist=sink)
    pipeline.handle_turn(turn("I'll handle the budget review soon.", turn_id="turn-a"))
    pipeline.handle_turn(
        turn(
            "Maybe the budget review sometime, I'm not sure.",
            turn_id="turn-b",
            created_at="2026-09-26T00:01:00Z",
        )
    )

    opened, closed = sink.handoffs
    assert opened.transition.from_status is None
    assert opened.transition.to_status == "AWAITING_CLARIFICATION"
    assert opened.speech_action == "CLARIFY"
    assert closed.transition.from_status == "AWAITING_CLARIFICATION"
    assert closed.transition.to_status == "UNRESOLVED_AMBIGUOUS"
    assert closed.commitment is not None
    assert opened.commitment is not None
    assert closed.commitment["commitment_id"] == opened.commitment["commitment_id"]
    assert "turn-b" in closed.commitment["source_turn_ids"]
    assert closed.intervened_this_turn is False


def test_handle_turn_payload_response_omits_handoff():
    sink = RecordingPersistSink()
    pipeline = BrainPipeline(persist=sink)
    body = handle_turn_payload(
        {
            "session_id": "sess-api",
            "turn_id": "turn-1",
            "speaker_role": "user",
            "text": "I will send the proposal by Friday.",
            "conversation_id": "conv-api",
            "intervened": True,
            "intervention_reason": "hold confirmation",
        },
        pipeline=pipeline,
    )
    assert set(body) == TURN_RESULT_KEYS
    assert "persist_handoff" not in body
    assert "conversation_id" not in body
    assert len(sink.handoffs) == 1
    assert sink.handoffs[0].conversation_id == "conv-api"
    assert sink.handoffs[0].intervened_this_turn is True
    assert sink.handoffs[0].intervention_reason == "hold confirmation"
    assert sink.handoffs[0].transition.to_status == sink.handoffs[0].commitment["status"]


def test_get_pipeline_installs_persist_sink(monkeypatch):
    import intelligence.api as api

    monkeypatch.setattr(api, "_default_pipeline", None)
    sink = RecordingPersistSink()
    pipeline = get_pipeline(persist=sink)
    pipeline.handle_turn(turn("Got it.", turn_id="gp-1"))
    assert pipeline.persist is sink
    assert len(sink.handoffs) == 1

    replacement = RecordingPersistSink()
    same = get_pipeline(persist=replacement)
    assert same is pipeline
    assert same.persist is replacement


def test_create_app_persist_is_side_effect_only():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    sink = RecordingPersistSink()
    app = create_app(persist=sink)
    client = TestClient(app)

    clear = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-http",
            "turn_id": "turn-1",
            "speaker_role": "user",
            "text": "I will send the proposal by Friday.",
            "conversation_id": "conv-http",
        },
    )
    assert clear.status_code == 200
    payload = clear.json()
    assert set(payload) == TURN_RESULT_KEYS
    assert payload["speech_action"] == "SILENT"
    assert payload["commitment"]["status"] == "CONFIRMED"
    assert len(sink.handoffs) == 1
    assert sink.handoffs[0].conversation_id == "conv-http"
    assert sink.handoffs[0].transition.to_status == "CONFIRMED"

    skipped = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-http",
            "turn_id": "turn-2",
            "speaker_role": "user",
            "text": "um",
        },
    )
    assert skipped.status_code == 200
    assert skipped.json()["skipped_by_filter"] is True
    assert "persist_handoff" not in skipped.json()
    assert len(sink.handoffs) == 2
    assert sink.handoffs[1].commitment is None
    assert sink.handoffs[1].transition.to_status == "NO_COMMITMENT"
