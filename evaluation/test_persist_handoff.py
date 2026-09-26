"""Brain → Vault PersistHandoff end-to-end. Fixture Brain, in-memory SQLite.

Golden YAML still scores ``TurnResult`` only. These tests pass a
``pipeline_factory`` of ``BrainPipeline(fixture_mode=True, persist=SqlitePersistSink)``
and an observer that reads ``evidence.provenance.get_commitment_evidence``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evidence.provenance import get_commitment_evidence
from storage.db import connect, init_schema

from evaluation.harness import ScenarioResult, load_scenarios, run_scenario
from evaluation.persist_observer import PersistHandoffSession, sink_connection

_TURN_RESULT_KEYS = {
    "speech_action",
    "clarification_question",
    "skipped_by_filter",
    "filter_reason",
    "policy_notes",
    "commitment",
    "reasoning",
}


def _scenario(scenario_id: str):
    return next(item for item in load_scenarios() if item.id == scenario_id)


def _run(scenario_id: str) -> tuple[PersistHandoffSession, ScenarioResult]:
    session = PersistHandoffSession()
    result = run_scenario(
        _scenario(scenario_id),
        pipeline_factory=session.factory,
        observers=[session],
    )
    return session, result


def test_clear_commitment_confirmed_silent_evidence():
    """Clear commitment → CONFIRMED + SILENT, and the evidence chain cites the turn."""
    session, result = _run("clear_commitment")
    assert result.passed, result.render()

    first = session.observation("clear_commitment", "turn-1")
    assert first.speech_action == "SILENT"
    assert first.status == "CONFIRMED"
    assert first.db_status == "CONFIRMED"
    assert first.skipped_by_filter is False
    assert "turn-1" in first.source_turn_ids
    assert first.event_count >= 1
    assert first.turn_persisted is True

    restated = session.observation("clear_commitment", "turn-2")
    assert restated.status == "CONFIRMED"
    assert restated.db_status == "CONFIRMED"
    assert restated.commitment_id == first.commitment_id
    assert "turn-1" in restated.source_turn_ids
    assert "turn-2" in restated.source_turn_ids


def test_clarify_path_awaiting_clarification_evidence():
    """Ambiguous commitment → AWAITING_CLARIFICATION with a Vault evidence chain."""
    session, result = _run("clarification_then_confirm")
    assert result.passed, result.render()

    asked = session.observation("clarification_then_confirm", "turn-1")
    assert asked.speech_action == "CLARIFY"
    assert asked.status == "AWAITING_CLARIFICATION"
    assert asked.db_status == "AWAITING_CLARIFICATION"
    assert asked.commitment_id is not None
    assert "turn-1" in asked.source_turn_ids
    assert asked.event_count >= 1

    confirmed = session.observation("clarification_then_confirm", "turn-2")
    assert confirmed.speech_action == "SILENT"
    assert confirmed.status == "CONFIRMED"
    assert confirmed.db_status == "CONFIRMED"
    assert confirmed.commitment_id == asked.commitment_id
    assert "turn-2" in confirmed.source_turn_ids


def test_filter_skip_um_persists_turn_without_commitment():
    """Filler ``um`` is skipped_by_filter, commitment stays null, the turn row remains."""
    session, result = _run("filter_skip")
    assert result.passed, result.render()

    filler = session.observation("filter_skip", "turn-filler")
    assert filler.speech_action == "SILENT"
    assert filler.skipped_by_filter is True
    assert filler.status is None
    assert filler.commitment_id is None
    assert filler.db_status is None
    assert filler.turn_persisted is True
    assert filler.source_turn_ids == []

    conn = session.connection_for("filter_skip")
    row = conn.execute(
        "SELECT content FROM turns WHERE turn_id = ?",
        ("turn-filler",),
    ).fetchone()
    assert row is not None
    assert row["content"] == "um"
    empty = session.observation("filter_skip", "turn-empty")
    duplicate = session.observation("filter_skip", "turn-duplicate")
    assert empty.skipped_by_filter is True and empty.commitment_id is None
    assert duplicate.skipped_by_filter is True and duplicate.commitment_id is None
    assert empty.turn_persisted is True
    assert duplicate.turn_persisted is True

    acknowledged = session.observation("filter_skip", "turn-ack")
    assert acknowledged.skipped_by_filter is False
    assert acknowledged.status == "NO_COMMITMENT"
    assert acknowledged.db_status == "NO_COMMITMENT"
    assert "turn-ack" in acknowledged.source_turn_ids


def test_intervened_clear_commitment_detected_not_confirmed():
    """A clear commitment spoken while intervened stays DETECTED, and evidence says so."""
    session, result = _run("intervened_blocks_confirm")
    assert result.passed, result.render()

    blocked = session.observation("intervened_blocks_confirm", "turn-1")
    assert blocked.speech_action == "SILENT"
    assert blocked.status == "DETECTED"
    assert blocked.db_status == "DETECTED"
    assert blocked.intervened is True
    assert "turn-1" in blocked.source_turn_ids
    assert "turn-1" in blocked.intervention_turn_ids
    assert blocked.event_count >= 1

    held = session.observation("intervened_blocks_confirm", "turn-2")
    assert held.status == "DETECTED"
    assert held.db_status == "DETECTED"
    assert held.intervened is True
    assert held.commitment_id == blocked.commitment_id
    assert "turn-2" in held.source_turn_ids
    assert held.intervention_turn_ids == ["turn-1"]


def test_evidence_reads_the_sink_connection_not_a_second_database():
    """A new ``connect(':memory:')`` is empty. Evidence must use the sink's conn."""
    session, result = _run("clear_commitment")
    assert result.passed, result.render()

    conn = session.connection_for("clear_commitment")
    used = {
        item
        for scenario_id, _turn_id, item in session.evidence_connections
        if scenario_id == "clear_commitment"
    }
    assert used == {conn}

    commitment_id = session.observation("clear_commitment", "turn-1").commitment_id
    assert commitment_id is not None
    other = connect(":memory:")
    try:
        init_schema(other)
        assert other is not conn
        with pytest.raises(KeyError):
            get_commitment_evidence(commitment_id, other)
    finally:
        other.close()

    evidence = get_commitment_evidence(commitment_id, conn)
    assert evidence["commitment"].status == "CONFIRMED"
    assert "turn-1" in evidence["commitment"].source_turn_ids


def test_every_golden_dialog_persists_through_the_same_sink():
    """The same factory and observer cover the whole golden catalog, one DB per file."""
    scenarios = load_scenarios()
    session = PersistHandoffSession()
    failures: list[str] = []
    for scenario in scenarios:
        result = run_scenario(
            scenario,
            pipeline_factory=session.factory,
            observers=[session],
        )
        if not result.passed:
            failures.append(result.render())
    assert failures == []
    assert {item.scenario_id for item in session.observations} == {
        scenario.id for scenario in scenarios
    }


def test_demo_post_turn_returns_turn_result_json(tmp_path: Path):
    """Demo ``create_app(persist=SqlitePersistSink)`` still returns TurnResult JSON.

    Fixture Brain only. This does not drive the Glass page.
    """
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from frontend.demo_server import create_demo_app

    app = create_demo_app(tmp_path / "demo.sqlite")
    # Glass hands one connection to the sink and to get_commitment_evidence.
    assert sink_connection(app.state.pipeline) is app.state.db_conn
    client = TestClient(app)

    status = client.get("/v1/demo/status")
    assert status.status_code == 200
    assert status.json()["fixture_mode"] is True
    assert status.json()["persist"] == "SqlitePersistSink"

    turn_id = "turn-demo-clear"
    clear = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-demo-eval",
            "turn_id": turn_id,
            "speaker_role": "user",
            "text": "I will send the proposal by Friday.",
            "created_at": "2026-09-26T00:00:00Z",
        },
    )
    assert clear.status_code == 200
    body = clear.json()
    assert _TURN_RESULT_KEYS <= set(body)
    assert body["speech_action"] == "SILENT"
    assert body["skipped_by_filter"] is False
    assert isinstance(body["policy_notes"], list)
    commitment = body["commitment"]
    assert isinstance(commitment, dict)
    assert commitment["status"] == "CONFIRMED"
    assert turn_id in commitment["source_turn_ids"]

    evidence = client.get(f"/v1/demo/evidence/{commitment['commitment_id']}")
    assert evidence.status_code == 200
    chain = evidence.json()
    assert chain["available"] is True
    assert chain["commitment"]["status"] == "CONFIRMED"
    assert turn_id in chain["commitment"]["source_turn_ids"]
    assert any(item["turn_id"] == turn_id for item in chain["source_turns"])
    direct = get_commitment_evidence(commitment["commitment_id"], app.state.db_conn)
    assert direct["commitment"].status == "CONFIRMED"
    assert any(item.turn_id == turn_id for item in direct["source_turns"])

    filler = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-demo-eval",
            "turn_id": "turn-demo-um",
            "speaker_role": "user",
            "text": "um",
            "created_at": "2026-09-26T00:01:00Z",
        },
    )
    assert filler.status_code == 200
    skipped = filler.json()
    assert _TURN_RESULT_KEYS <= set(skipped)
    assert skipped["speech_action"] == "SILENT"
    assert skipped["skipped_by_filter"] is True
    assert skipped["commitment"] is None
    assert skipped["filter_reason"] == "filler"

    snapshot = client.get("/v1/sessions/sess-demo-eval")
    assert snapshot.status_code == 200
    recorded = {item["turn_id"] for item in snapshot.json()["turns"]}
    assert recorded == {"turn-demo-clear", "turn-demo-um"}
