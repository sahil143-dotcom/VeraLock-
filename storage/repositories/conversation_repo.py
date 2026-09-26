"""Conversation repository."""

from __future__ import annotations

import sqlite3
from typing import Optional

from storage.models import ConversationRow


class ConversationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        conversation_id: str,
        created_at: str,
        updated_at: str,
        session_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ConversationRow:
        self._conn.execute(
            """
            INSERT INTO conversations (conversation_id, session_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conversation_id, session_id, title, created_at, updated_at),
        )
        self._conn.commit()
        row = self.get(conversation_id)
        assert row is not None
        return row

    def get(self, conversation_id: str) -> Optional[ConversationRow]:
        cur = self._conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        )
        row = cur.fetchone()
        return ConversationRow.from_row(row) if row else None
