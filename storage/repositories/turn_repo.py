"""Turn repository."""

from __future__ import annotations

import sqlite3
from typing import Optional

from storage.models import TurnRow


class TurnRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        turn_id: str,
        conversation_id: str,
        speaker_role: str,
        content: str,
        created_at: str,
    ) -> TurnRow:
        self._conn.execute(
            """
            INSERT INTO turns (turn_id, conversation_id, speaker_role, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (turn_id, conversation_id, speaker_role, content, created_at),
        )
        self._conn.commit()
        row = self.get(turn_id)
        assert row is not None
        return row

    def get(self, turn_id: str) -> Optional[TurnRow]:
        cur = self._conn.execute("SELECT * FROM turns WHERE turn_id = ?", (turn_id,))
        row = cur.fetchone()
        return TurnRow.from_row(row) if row else None

    def get_many(self, turn_ids: list[str]) -> list[TurnRow]:
        if not turn_ids:
            return []
        # Preserve requested order.
        placeholders = ",".join("?" for _ in turn_ids)
        cur = self._conn.execute(
            f"SELECT * FROM turns WHERE turn_id IN ({placeholders})",
            tuple(turn_ids),
        )
        by_id = {r["turn_id"]: TurnRow.from_row(r) for r in cur.fetchall()}
        return [by_id[tid] for tid in turn_ids if tid in by_id]

    def list_for_conversation(self, conversation_id: str) -> list[TurnRow]:
        cur = self._conn.execute(
            "SELECT * FROM turns WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        )
        return [TurnRow.from_row(r) for r in cur.fetchall()]
