"""Follow-up scheduler — due checks against (optionally warped) now."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from followup.messages import render_followup_message
from followup.time_warp import get_now_iso
from storage.models import CommitmentRow
from storage.repositories.commitment_repo import CommitmentRepo


@dataclass
class DueFollowup:
    commitment: CommitmentRow
    message: str
    as_of: str
    overdue: bool


class FollowupScheduler:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._repo = CommitmentRepo(conn)

    def due(self, as_of: Optional[str] = None) -> list[DueFollowup]:
        """Return CONFIRMED commitments whose next_followup_at <= as_of."""
        now_iso = as_of or get_now_iso()
        rows = self._repo.list_due_followups(now_iso)
        results: list[DueFollowup] = []
        for row in rows:
            overdue = bool(row.due_at and row.due_at <= now_iso)
            msg = render_followup_message(row, overdue=overdue)
            results.append(
                DueFollowup(commitment=row, message=msg, as_of=now_iso, overdue=overdue)
            )
        return results

    def schedule(
        self,
        commitment_id: str,
        next_followup_at: str,
        updated_at: Optional[str] = None,
    ) -> CommitmentRow:
        return self._repo.set_followup(
            commitment_id,
            next_followup_at,
            updated_at or get_now_iso(),
        )
