#!/usr/bin/env python3
"""End-to-end smoke test for Veralock Vault persistence layer.

Run from repo root:
  python scripts/smoke_vault.py

Must exit 0, print evidence chain, and show time-warp follow-up due.
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

# Allow imports when run as scripts/smoke_vault.py from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evidence.provenance import format_evidence_chain, get_commitment_evidence
from followup.scheduler import FollowupScheduler
from followup.time_warp import TimeWarp, get_now, get_now_iso, real_now_iso
from shared.commitment_schema import CommitmentStatus, ConditionStatus
from storage.db import connect, init_schema
from storage.repositories import (
    CommitmentRepo,
    ConversationRepo,
    InterventionRepo,
    TurnRepo,
)


def _uid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4()}"


def main() -> int:
    TimeWarp.reset()
    db_file = Path(tempfile.gettempdir()) / f"veralock_smoke_{uuid.uuid4().hex}.db"
    print(f"smoke db: {db_file}")

    conn = connect(db_file)
    init_schema(conn)

    conversations = ConversationRepo(conn)
    turns = TurnRepo(conn)
    commitments = CommitmentRepo(conn)
    interventions = InterventionRepo(conn)
    scheduler = FollowupScheduler(conn)

    t0 = real_now_iso()
    session_id = _uid("sess-")
    conversation_id = _uid("conv-")
    conversations.create(
        conversation_id=conversation_id,
        session_id=session_id,
        title="Smoke: ship-by Friday",
        created_at=t0,
        updated_at=t0,
    )

    turn1 = _uid("turn-")
    turn2 = _uid("turn-")
    turn3 = _uid("turn-")

    turns.create(
        turn_id=turn1,
        conversation_id=conversation_id,
        speaker_role="user",
        content="I'll send the proposal by Friday if legal signs off.",
        created_at=t0,
    )
    turns.create(
        turn_id=turn2,
        conversation_id=conversation_id,
        speaker_role="counterpart",
        content="Got it — please confirm once legal reviews.",
        created_at=t0,
    )
    turns.create(
        turn_id=turn3,
        conversation_id=conversation_id,
        speaker_role="user",
        content="Confirmed: I'll send it Friday after legal signs.",
        created_at=t0,
    )

    commitment_id = _uid("cmt-")
    commitments.create(
        commitment_id=commitment_id,
        topic_id="proposal-delivery",
        status=CommitmentStatus.DETECTED,
        speaker_role="user",
        canonical_text="Send the proposal by Friday if legal signs off.",
        raw_span="I'll send the proposal by Friday if legal signs off.",
        source_turn_ids=[turn1],
        clarification_count=0,
        intervened=False,
        is_acknowledgement=False,
        is_intention_only=False,
        confidence=0.82,
        conditions={"legal_signoff": True},
        created_at=t0,
        updated_at=t0,
        session_id=session_id,
        conversation_id=conversation_id,
        condition="legal signs off",
        dependency_owner="legal",
        condition_status=ConditionStatus.PENDING.value,
    )

    t1 = real_now_iso()
    commitments.update_status(
        commitment_id,
        CommitmentStatus.AWAITING_CLARIFICATION,
        source_turn_ids=[turn1, turn2],
        updated_at=t1,
        note="counterpart asked for confirmation",
        clarification_count=1,
    )

    # Intervention recorded (must NOT auto-promote; we still confirm via explicit turn).
    interventions.create(
        commitment_id=commitment_id,
        source_turn_ids=[turn2],
        created_at=t1,
        reason="clarification requested by counterpart",
    )

    # Schedule follow-up ~24h from real now (will become due after +48h warp).
    followup_at = (get_now() + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    due_at = (get_now() + timedelta(hours=36)).isoformat().replace("+00:00", "Z")

    t2 = real_now_iso()
    commitments.update_status(
        commitment_id,
        CommitmentStatus.CONFIRMED,
        source_turn_ids=[turn1, turn2, turn3],
        updated_at=t2,
        note="user confirmed after clarification",
        next_followup_at=followup_at,
        due_at=due_at,
        condition_status=ConditionStatus.PENDING.value,
    )

    # Before warp: should NOT be due.
    due_before = scheduler.due(as_of=real_now_iso())
    print(f"\ndue before warp: {len(due_before)} (expect 0)")
    assert len(due_before) == 0, "follow-up should not be due before time-warp"

    # FAST-FORWARD 48 HOURS — follow-up becomes due.
    print("\n>>> TIME-WARP: FAST-FORWARD 48 HOURS")
    with TimeWarp(hours=48):
        warped = get_now_iso()
        print(f"real now:   {real_now_iso()}")
        print(f"warped now: {warped}")
        due_after = scheduler.due()
        print(f"due after warp: {len(due_after)} (expect 1)")
        assert len(due_after) == 1, "follow-up must be due after 48h time-warp"
        item = due_after[0]
        print("\n--- follow-up message ---")
        print(item.message)
        print(f"overdue flag: {item.overdue}")
        assert item.commitment.commitment_id == commitment_id

    evidence = get_commitment_evidence(commitment_id, conn)
    print()
    print(format_evidence_chain(evidence))

    # Sanity: events carry source_turn_ids
    assert evidence["events"], "expected commitment_events"
    for ev in evidence["events"]:
        assert isinstance(ev.source_turn_ids, list) and ev.source_turn_ids, (
            f"event {ev.event_id} missing source_turn_ids"
        )
    assert evidence["source_turns"], "expected source turns in evidence"

    conn.close()
    try:
        db_file.unlink(missing_ok=True)
    except OSError:
        pass

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
