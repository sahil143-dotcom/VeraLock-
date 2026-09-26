"""Intervention event repository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Optional

from storage.models import InterventionEventRow


class InterventionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        commitment_id: str,
        source_turn_ids: list[str],
        created_at: str,
        reason: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> InterventionEventRow:
        eid = event_id or str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO intervention_events
                (event_id, commitment_id, source_turn_ids, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (eid, commitment_id, json.dumps(list(source_turn_ids)), reason, created_at),
        )
        self._conn.commit()
        row = self.get(eid)
        assert row is not None
        return row

    def get(self, event_id: str) -> Optional[InterventionEventRow]:
        cur = self._conn.execute(
            "SELECT * FROM intervention_events WHERE event_id = ?",
            (event_id,),
        )
        row = cur.fetchone()
        return InterventionEventRow.from_row(row) if row else None

    def list_for_commitment(self, commitment_id: str) -> list[InterventionEventRow]:
        cur = self._conn.execute(
            """
            SELECT * FROM intervention_events
            WHERE commitment_id = ?
            ORDER BY created_at ASC
            """,
            (commitment_id,),
        )
        return [InterventionEventRow.from_row(r) for r in cur.fetchall()]
