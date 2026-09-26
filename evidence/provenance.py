"""Provenance: commitment + events + source turns evidence chain."""

from __future__ import annotations

import sqlite3
from typing import Any

from storage.models import CommitmentEventRow, CommitmentRow, InterventionEventRow, TurnRow
from storage.repositories.commitment_repo import CommitmentRepo
from storage.repositories.intervention_repo import InterventionRepo
from storage.repositories.turn_repo import TurnRepo


def get_commitment_evidence(
    commitment_id: str,
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Return commitment + events + source turns for a commitment_id.

    Structure:
      {
        "commitment": CommitmentRow,
        "events": [CommitmentEventRow, ...],
        "intervention_events": [InterventionEventRow, ...],
        "source_turns": [TurnRow, ...],  # union of all source_turn_ids in order
      }
    """
    commitment_repo = CommitmentRepo(conn)
    intervention_repo = InterventionRepo(conn)
    turn_repo = TurnRepo(conn)

    commitment = commitment_repo.get(commitment_id)
    if commitment is None:
        raise KeyError(f"commitment not found: {commitment_id}")

    events = commitment_repo.list_events(commitment_id)
    interventions = intervention_repo.list_for_commitment(commitment_id)

    seen: set[str] = set()
    ordered_ids: list[str] = []
    for tid in commitment.source_turn_ids:
        if tid not in seen:
            seen.add(tid)
            ordered_ids.append(tid)
    for ev in events:
        for tid in ev.source_turn_ids:
            if tid not in seen:
                seen.add(tid)
                ordered_ids.append(tid)
    for ie in interventions:
        for tid in ie.source_turn_ids:
            if tid not in seen:
                seen.add(tid)
                ordered_ids.append(tid)

    source_turns = turn_repo.get_many(ordered_ids)

    return {
        "commitment": commitment,
        "events": events,
        "intervention_events": interventions,
        "source_turns": source_turns,
    }


def format_evidence_chain(evidence: dict[str, Any]) -> str:
    """Human-readable evidence chain for smoke / demos."""
    c: CommitmentRow = evidence["commitment"]
    lines: list[str] = []
    lines.append("=== EVIDENCE CHAIN ===")
    lines.append(f"commitment_id: {c.commitment_id}")
    lines.append(f"status:        {c.status}")
    lines.append(f"canonical:     {c.canonical_text}")
    lines.append(f"source_turn_ids: {c.source_turn_ids}")
    if c.condition:
        lines.append(f"condition:     {c.condition} ({c.condition_status})")
    if c.next_followup_at:
        lines.append(f"next_followup_at: {c.next_followup_at}")
    lines.append("")
    lines.append("--- commitment_events ---")
    for ev in evidence["events"]:
        assert isinstance(ev, CommitmentEventRow)
        lines.append(
            f"  [{ev.created_at}] {ev.from_status} -> {ev.to_status} "
            f"turns={ev.source_turn_ids} note={ev.note!r}"
        )
    if evidence["intervention_events"]:
        lines.append("--- intervention_events ---")
        for ie in evidence["intervention_events"]:
            assert isinstance(ie, InterventionEventRow)
            lines.append(
                f"  [{ie.created_at}] reason={ie.reason!r} turns={ie.source_turn_ids}"
            )
    lines.append("--- source_turns ---")
    for t in evidence["source_turns"]:
        assert isinstance(t, TurnRow)
        lines.append(f"  [{t.turn_id}] {t.speaker_role}: {t.content}")
    return "\n".join(lines)
