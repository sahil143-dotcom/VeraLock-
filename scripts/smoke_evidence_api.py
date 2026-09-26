#!/usr/bin/env python3
"""Smoke test for Vault evidence HTTP API.

Run from repo root:
  python scripts/smoke_evidence_api.py

Seeds a temp SQLite via SqlitePersistSink, hits create_evidence_app with
TestClient, expects exit 0 and prints SMOKE OK.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        from fastapi.testclient import TestClient
    except ImportError as exc:
        print(f"SMOKE FAIL: missing dependency ({exc})")
        print("Install: pip install fastapi httpx")
        return 1

    from evidence.api import create_evidence_app
    from shared.persist_handoff import PersistHandoff, Transition
    from storage.db import connect, init_schema
    from storage.persist_sink import SqlitePersistSink

    # check_same_thread=False: TestClient runs sync routes on a worker thread.
    conn = connect(":memory:", check_same_thread=False)
    init_schema(conn)
    sink = SqlitePersistSink(conn)

    session_id = f"sess-{uuid.uuid4()}"
    turn_id = f"turn-{uuid.uuid4()}"
    commitment_id = f"cmt-{uuid.uuid4()}"
    now = "2026-09-26T12:00:00Z"

    handoff = PersistHandoff(
        session_id=session_id,
        turn_id=turn_id,
        speaker_role="user",
        turn_text="I will send the proposal by Friday.",
        created_at=now,
        speech_action="SILENT",
        clarification_question=None,
        skipped_by_filter=False,
        policy_notes=[],
        commitment={
            "commitment_id": commitment_id,
            "topic_id": "proposal-delivery",
            "status": "CONFIRMED",
            "speaker_role": "user",
            "canonical_text": "Send the proposal by Friday.",
            "raw_span": "I will send the proposal by Friday.",
            "source_turn_ids": [turn_id],
            "clarification_count": 0,
            "intervened": False,
            "is_acknowledgement": False,
            "is_intention_only": False,
            "confidence": 0.9,
            "conditions": None,
            "created_at": now,
            "updated_at": now,
            "session_id": session_id,
        },
        transition=Transition(from_status=None, to_status="CONFIRMED"),
        intervened_this_turn=False,
    )
    sink.persist(handoff)

    app = create_evidence_app(conn=conn)
    client = TestClient(app)

    health = client.get("/v1/health")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["status"] == "ok"
    assert body["component"] == "vault-evidence"
    print(f"GET /v1/health -> {body}")

    missing = client.get("/v1/evidence/does-not-exist")
    assert missing.status_code == 404, missing.text
    print("GET /v1/evidence/does-not-exist -> 404")

    ok = client.get(f"/v1/evidence/{commitment_id}")
    assert ok.status_code == 200, ok.text
    payload = ok.json()
    assert isinstance(payload["commitment"], dict)
    assert payload["commitment"]["commitment_id"] == commitment_id
    assert payload["commitment"]["status"] == "CONFIRMED"
    assert isinstance(payload["events"], list) and payload["events"]
    assert isinstance(payload["intervention_events"], list)
    assert isinstance(payload["source_turns"], list) and payload["source_turns"]
    assert payload["source_turns"][0]["turn_id"] == turn_id
    assert isinstance(payload["events"][0], dict)
    print(
        f"GET /v1/evidence/{{id}} -> commitment={commitment_id} "
        f"events={len(payload['events'])} turns={len(payload['source_turns'])}"
    )

    conn.close()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
