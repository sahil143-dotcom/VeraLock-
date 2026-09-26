"""Commitment repository — update_status appends a commitment_event with source_turn_ids."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Optional

from shared.commitment_schema import CommitmentStatus
from storage.models import CommitmentEventRow, CommitmentRow


class CommitmentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        *,
        commitment_id: str,
        topic_id: str,
        status: CommitmentStatus | str,
        speaker_role: str,
        canonical_text: str,
        source_turn_ids: list[str],
        created_at: str,
        updated_at: str,
        raw_span: Optional[str] = None,
        clarification_count: int = 0,
        intervened: bool = False,
        is_acknowledgement: bool = False,
        is_intention_only: bool = False,
        confidence: float = 0.0,
        conditions: Optional[dict[str, Any]] = None,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        condition: Optional[str] = None,
        dependency_owner: Optional[str] = None,
        condition_status: Optional[str] = None,
        due_at: Optional[str] = None,
        next_followup_at: Optional[str] = None,
        superseded_by_commitment_id: Optional[str] = None,
    ) -> CommitmentRow:
        status_val = status.value if isinstance(status, CommitmentStatus) else str(status)
        self._conn.execute(
            """
            INSERT INTO commitments (
                commitment_id, topic_id, status, speaker_role, canonical_text, raw_span,
                source_turn_ids, clarification_count, intervened, is_acknowledgement,
                is_intention_only, confidence, conditions, created_at, updated_at, session_id,
                conversation_id, condition, dependency_owner, condition_status,
                due_at, next_followup_at, superseded_by_commitment_id
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            """,
            (
                commitment_id,
                topic_id,
                status_val,
                speaker_role,
                canonical_text,
                raw_span,
                json.dumps(list(source_turn_ids)),
                clarification_count,
                int(intervened),
                int(is_acknowledgement),
                int(is_intention_only),
                confidence,
                json.dumps(conditions) if conditions is not None else None,
                created_at,
                updated_at,
                session_id,
                conversation_id,
                condition,
                dependency_owner,
                condition_status,
                due_at,
                next_followup_at,
                superseded_by_commitment_id,
            ),
        )
        # Initial creation event for provenance chain.
        self._append_event(
            commitment_id=commitment_id,
            from_status=None,
            to_status=status_val,
            source_turn_ids=source_turn_ids,
            created_at=created_at,
            note="created",
        )
        self._conn.commit()
        row = self.get(commitment_id)
        assert row is not None
        return row

    def get(self, commitment_id: str) -> Optional[CommitmentRow]:
        cur = self._conn.execute(
            "SELECT * FROM commitments WHERE commitment_id = ?",
            (commitment_id,),
        )
        row = cur.fetchone()
        return CommitmentRow.from_row(row) if row else None

    def update_status(
        self,
        commitment_id: str,
        new_status: CommitmentStatus | str,
        source_turn_ids: list[str],
        updated_at: str,
        note: Optional[str] = None,
        *,
        next_followup_at: Optional[str] = None,
        due_at: Optional[str] = None,
        condition_status: Optional[str] = None,
        superseded_by_commitment_id: Optional[str] = None,
        intervened: Optional[bool] = None,
        clarification_count: Optional[int] = None,
    ) -> CommitmentRow:
        """Update status and append a commitment_event with source_turn_ids."""
        current = self.get(commitment_id)
        if current is None:
            raise KeyError(f"commitment not found: {commitment_id}")

        to_status = (
            new_status.value if isinstance(new_status, CommitmentStatus) else str(new_status)
        )
        from_status = current.status

        sets = ["status = ?", "updated_at = ?", "source_turn_ids = ?"]
        params: list[Any] = [to_status, updated_at, json.dumps(list(source_turn_ids))]

        if next_followup_at is not None:
            sets.append("next_followup_at = ?")
            params.append(next_followup_at)
        if due_at is not None:
            sets.append("due_at = ?")
            params.append(due_at)
        if condition_status is not None:
            sets.append("condition_status = ?")
            params.append(condition_status)
        if superseded_by_commitment_id is not None:
            sets.append("superseded_by_commitment_id = ?")
            params.append(superseded_by_commitment_id)
        if intervened is not None:
            sets.append("intervened = ?")
            params.append(int(intervened))
        if clarification_count is not None:
            sets.append("clarification_count = ?")
            params.append(clarification_count)

        params.append(commitment_id)
        self._conn.execute(
            f"UPDATE commitments SET {', '.join(sets)} WHERE commitment_id = ?",
            tuple(params),
        )
        self._append_event(
            commitment_id=commitment_id,
            from_status=from_status,
            to_status=to_status,
            source_turn_ids=source_turn_ids,
            created_at=updated_at,
            note=note,
        )
        self._conn.commit()
        row = self.get(commitment_id)
        assert row is not None
        return row

    def set_followup(
        self,
        commitment_id: str,
        next_followup_at: str,
        updated_at: str,
    ) -> CommitmentRow:
        self._conn.execute(
            """
            UPDATE commitments
            SET next_followup_at = ?, updated_at = ?
            WHERE commitment_id = ?
            """,
            (next_followup_at, updated_at, commitment_id),
        )
        self._conn.commit()
        row = self.get(commitment_id)
        assert row is not None
        return row

    def list_due_followups(self, as_of: str) -> list[CommitmentRow]:
        cur = self._conn.execute(
            """
            SELECT * FROM commitments
            WHERE next_followup_at IS NOT NULL
              AND next_followup_at <= ?
              AND status = ?
            ORDER BY next_followup_at ASC
            """,
            (as_of, CommitmentStatus.CONFIRMED.value),
        )
        return [CommitmentRow.from_row(r) for r in cur.fetchall()]

    def list_events(self, commitment_id: str) -> list[CommitmentEventRow]:
        cur = self._conn.execute(
            """
            SELECT * FROM commitment_events
            WHERE commitment_id = ?
            ORDER BY created_at ASC
            """,
            (commitment_id,),
        )
        return [CommitmentEventRow.from_row(r) for r in cur.fetchall()]

    def _append_event(
        self,
        *,
        commitment_id: str,
        from_status: Optional[str],
        to_status: str,
        source_turn_ids: list[str],
        created_at: str,
        note: Optional[str] = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO commitment_events
                (event_id, commitment_id, from_status, to_status, source_turn_ids, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                commitment_id,
                from_status,
                to_status,
                json.dumps(list(source_turn_ids)),
                note,
                created_at,
            ),
        )
