"""Conversation repository."""

from __future__ import annotations

import sqlite3
import uuid
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

    def get_by_session_id(self, session_id: str) -> Optional[ConversationRow]:
        """Return the most recently created conversation for a Brain session_id."""
        cur = self._conn.execute(
            """
            SELECT * FROM conversations
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        row = cur.fetchone()
        return ConversationRow.from_row(row) if row else None

    def ensure_for_session(
        self,
        session_id: str,
        created_at: str,
        conversation_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> ConversationRow:
        """Resolve conversation_id if given; else find-or-create by session_id."""
        if conversation_id:
            existing = self.get(conversation_id)
            if existing is not None:
                return existing
            return self.create(
                conversation_id=conversation_id,
                session_id=session_id,
                title=title,
                created_at=created_at,
                updated_at=created_at,
            )

        by_session = self.get_by_session_id(session_id)
        if by_session is not None:
            return by_session

        return self.create(
            conversation_id=str(uuid.uuid4()),
            session_id=session_id,
            title=title,
            created_at=created_at,
            updated_at=created_at,
        )
