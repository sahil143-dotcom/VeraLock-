#!/usr/bin/env python3
"""Smoke: Brain PersistHandoff → SqlitePersistSink → evidence chain.

Run from repo root:
  python3 scripts/smoke_persist_handoff.py

Must exit 0 and print SMOKE OK.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evidence.provenance import format_evidence_chain, get_commitment_evidence
from followup.time_warp import real_now_iso
from shared.commitment_schema import CommitmentStatus
from shared.persist_handoff import PersistHandoff, Transition, build_handoff
from storage.db import connect, init_schema
from storage.persist_sink import SqlitePersistSink


def main() -> int:
    db_file = Path(tempfile.gettempdir()) / f"veralock_persist_smoke_{uuid.uuid4().hex}.db"
    print(f"smoke db: {db_file}")

    conn = connect(db_file)
    init_schema(conn)
    sink = SqlitePersistSink(conn)

    now = real_now_iso()
    session_id = f"sess-{uuid.uuid4()}"
    turn_id = f"turn-{uuid.uuid4()}"
    commitment_id = f"cmt-{uuid.uuid4()}"

    # Mimic StubLLM confirmed commitment created at CONFIRMED
    # (equivalent to DETECTED→CONFIRMED landing as a single create).
    commitment_dict = {
        "commitment_id": commitment_id,
        "topic_id": "proposal-delivery",
        "status": CommitmentStatus.CONFIRMED.value,
        "speaker_role": "user",
        "canonical_text": "Send the proposal by Friday.",
        "raw_span": "I'll send the proposal by Friday.",
        "source_turn_ids": [turn_id],
        "clarification_count": 0,
        "intervened": False,
        "is_acknowledgement": False,
        "is_intention_only": False,
        "confidence": 0.91,
        "conditions": None,
        "created_at": now,
        "updated_at": now,
        "session_id": session_id,
    }

    handoff = build_handoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role="user",
        turn_text="I'll send the proposal by Friday.",
        created_at=now,
        speech_action="SILENT",
        from_status=None,
        to_status=CommitmentStatus.CONFIRMED.value,
        clarification_question=None,
        skipped_by_filter=False,
        policy_notes=["stub_confirmed"],
        commitment=commitment_dict,
        intervened_this_turn=False,
    )

    # Round-trip serialization sanity.
    restored = PersistHandoff.from_dict(handoff.to_dict())
    assert restored.session_id == session_id
    assert restored.transition.to_status == CommitmentStatus.CONFIRMED.value

    sink.persist(handoff)

    # Idempotent re-persist of the same turn must not fail.
    sink.persist(handoff)

    evidence = get_commitment_evidence(commitment_id, conn)
    print()
    print(format_evidence_chain(evidence))

    assert evidence["commitment"].status == CommitmentStatus.CONFIRMED.value
    assert evidence["events"], "expected commitment_events (create)"
    assert evidence["source_turns"], "expected source turns in evidence"
    assert any(t.turn_id == turn_id for t in evidence["source_turns"])
    for ev in evidence["events"]:
        assert ev.source_turn_ids, f"event {ev.event_id} missing source_turn_ids"

    # Second scenario: DETECTED then transition to CONFIRMED via second handoff.
    session2 = f"sess-{uuid.uuid4()}"
    turn_a = f"turn-{uuid.uuid4()}"
    turn_b = f"turn-{uuid.uuid4()}"
    cmt2 = f"cmt-{uuid.uuid4()}"
    t0 = real_now_iso()

    detected = {
        "commitment_id": cmt2,
        "topic_id": "ship-docs",
        "status": CommitmentStatus.DETECTED.value,
        "speaker_role": "user",
        "canonical_text": "Ship the docs tomorrow.",
        "raw_span": "I'll ship the docs tomorrow.",
        "source_turn_ids": [turn_a],
        "clarification_count": 0,
        "intervened": False,
        "is_acknowledgement": False,
        "is_intention_only": False,
        "confidence": 0.7,
        "conditions": None,
        "created_at": t0,
        "updated_at": t0,
        "session_id": session2,
    }
    sink.persist(
        PersistHandoff(
            session_id=session2,
            turn_id=turn_a,
            speaker_role="user",
            turn_text="I'll ship the docs tomorrow.",
            created_at=t0,
            speech_action="SILENT",
            clarification_question=None,
            skipped_by_filter=False,
            policy_notes=["detected"],
            commitment=detected,
            transition=Transition(from_status=None, to_status="DETECTED"),
            intervened_this_turn=False,
        )
    )

    t1 = real_now_iso()
    confirmed = dict(detected)
    confirmed["status"] = CommitmentStatus.CONFIRMED.value
    confirmed["source_turn_ids"] = [turn_a, turn_b]
    confirmed["updated_at"] = t1
    confirmed["confidence"] = 0.95
    sink.persist(
        PersistHandoff(
            session_id=session2,
            turn_id=turn_b,
            speaker_role="user",
            turn_text="Confirmed — shipping tomorrow.",
            created_at=t1,
            speech_action="SILENT",
            clarification_question=None,
            skipped_by_filter=False,
            policy_notes=["confirmed"],
            commitment=confirmed,
            transition=Transition(from_status="DETECTED", to_status="CONFIRMED"),
            intervened_this_turn=False,
        )
    )

    evidence2 = get_commitment_evidence(cmt2, conn)
    assert evidence2["commitment"].status == "CONFIRMED"
    statuses = [(e.from_status, e.to_status) for e in evidence2["events"]]
    assert (None, "DETECTED") in statuses
    assert ("DETECTED", "CONFIRMED") in statuses
    assert len(evidence2["source_turns"]) >= 2

    conn.close()
    try:
        db_file.unlink(missing_ok=True)
    except OSError:
        pass

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
