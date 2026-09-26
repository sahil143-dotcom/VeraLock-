"""Tests for Brain→Vault PersistHandoff sink."""

from __future__ import annotations

import uuid

from evidence.provenance import get_commitment_evidence
from shared.persist_handoff import NullPersistPort, PersistHandoff, Transition, build_handoff
from storage.db import connect, init_schema
from storage.persist_sink import SqlitePersistSink
from storage.repositories.conversation_repo import ConversationRepo
from storage.repositories.turn_repo import TurnRepo


def _uid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


def test_null_persist_port_noops() -> None:
    port = NullPersistPort()
    handoff = build_handoff(
        session_id="s",
        turn_id="t",
        speaker_role="user",
        turn_text="hi",
        created_at="2026-01-01T00:00:00Z",
        speech_action="SILENT",
        from_status=None,
        to_status="NO_COMMITMENT",
        skipped_by_filter=True,
    )
    port.persist(handoff)  # must not raise


def test_sqlite_persist_sink_creates_conversation_turn_commitment() -> None:
    conn = connect(":memory:")
    init_schema(conn)
    sink = SqlitePersistSink(conn)

    session_id = _uid("sess-")
    turn_id = _uid("turn-")
    commitment_id = _uid("cmt-")
    now = "2026-09-26T10:00:00Z"

    commitment = {
        "commitment_id": commitment_id,
        "topic_id": "topic-a",
        "status": "CONFIRMED",
        "speaker_role": "user",
        "canonical_text": "Do the thing.",
        "raw_span": "I'll do the thing.",
        "source_turn_ids": [turn_id],
        "clarification_count": 0,
        "intervened": False,
        "is_acknowledgement": False,
        "is_intention_only": False,
        "confidence": 0.88,
        "conditions": None,
        "created_at": now,
        "updated_at": now,
        "session_id": session_id,
    }

    handoff = PersistHandoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role="user",
        turn_text="I'll do the thing.",
        created_at=now,
        speech_action="SILENT",
        clarification_question=None,
        skipped_by_filter=False,
        policy_notes=["ok"],
        commitment=commitment,
        transition=Transition(from_status=None, to_status="CONFIRMED"),
        intervened_this_turn=False,
    )
    sink.persist(handoff)

    # Idempotent turn
    sink.persist(handoff)

    conv = ConversationRepo(conn).get_by_session_id(session_id)
    assert conv is not None
    turn = TurnRepo(conn).get(turn_id)
    assert turn is not None
    assert turn.content == "I'll do the thing."

    evidence = get_commitment_evidence(commitment_id, conn)
    assert evidence["commitment"].status == "CONFIRMED"
    assert evidence["events"]
    assert evidence["source_turns"]
    assert evidence["source_turns"][0].turn_id == turn_id


def test_skipped_by_filter_persists_turn_only() -> None:
    conn = connect(":memory:")
    init_schema(conn)
    sink = SqlitePersistSink(conn)

    session_id = _uid("sess-")
    turn_id = _uid("turn-")
    now = "2026-09-26T11:00:00Z"

    sink.persist(
        build_handoff(
            session_id=session_id,
            turn_id=turn_id,
            speaker_role="user",
            turn_text="ok",
            created_at=now,
            speech_action="SILENT",
            from_status=None,
            to_status="NO_COMMITMENT",
            skipped_by_filter=True,
            policy_notes=["filter_skip"],
            commitment=None,
        )
    )

    assert ConversationRepo(conn).get_by_session_id(session_id) is not None
    assert TurnRepo(conn).get(turn_id) is not None
