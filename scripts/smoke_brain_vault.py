#!/usr/bin/env python3
"""End-to-end smoke test for Brain -> Vault persistence.

Run from the repository root:
    python3 scripts/smoke_brain_vault.py
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evidence.provenance import format_evidence_chain, get_commitment_evidence
from intelligence.models import TurnInput
from intelligence.pipeline import BrainPipeline
from shared.commitment_schema import CommitmentStatus
from storage.db import connect, init_schema
from storage.persist_sink import SqlitePersistSink


def turn(session_id: str, text: str) -> TurnInput:
    return TurnInput(
        session_id=session_id,
        turn_id=f"turn-{uuid.uuid4()}",
        speaker_role="user",
        text=text,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="veralock-brain-vault-") as tmpdir:
        db_path = Path(tmpdir) / "smoke.sqlite3"
        print(f"smoke db: {db_path}")
        conn = connect(db_path)
        try:
            init_schema(conn)
            sink = SqlitePersistSink(conn)
            pipeline = BrainPipeline(fixture_mode=True, persist=sink)

            print("\nA) CLEAR -> CONFIRMED")
            a_input = turn(
                f"session-{uuid.uuid4()}",
                "I will send the proposal by Friday.",
            )
            a_result = pipeline.handle_turn(a_input)
            assert a_result.commitment is not None
            assert a_result.commitment.status == CommitmentStatus.CONFIRMED
            print(f"status: {a_result.commitment.status.value}")
            a_evidence = get_commitment_evidence(a_result.commitment.commitment_id, conn)
            print(format_evidence_chain(a_evidence))

            print("\nB) CLARIFY")
            b_input = turn(
                f"session-{uuid.uuid4()}",
                "I'll handle the budget review soon.",
            )
            b_result = pipeline.handle_turn(b_input)
            assert b_result.commitment is not None
            assert b_result.commitment.status == CommitmentStatus.AWAITING_CLARIFICATION
            print(f"status: {b_result.commitment.status.value}")
            b_evidence = get_commitment_evidence(b_result.commitment.commitment_id, conn)
            print(format_evidence_chain(b_evidence))

            print("\nC) FILTER SKIP")
            c_session = f"session-{uuid.uuid4()}"
            c_prior = pipeline.handle_turn(
                turn(c_session, "I will review the budget tomorrow.")
            )
            assert c_prior.commitment is not None
            c_input = turn(c_session, "um")
            c_result = pipeline.handle_turn(c_input)
            assert c_result.skipped_by_filter is True
            assert c_result.commitment is None
            persisted = conn.execute(
                "SELECT 1 FROM turns WHERE turn_id = ?", (c_input.turn_id,)
            ).fetchone()
            assert persisted is not None
            turn_count = conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
            print(
                "skipped_by_filter=True; turn persisted without commitment "
                f"(total turns in DB: {turn_count})"
            )
        finally:
            conn.close()

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
