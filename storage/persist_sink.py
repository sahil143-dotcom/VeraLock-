"""SqlitePersistSink — Vault PersistPort implementation using existing repos.

Brain never imports this module; it depends only on shared.persist_handoff.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from shared.persist_handoff import PersistHandoff, PersistPort
from storage.repositories.commitment_repo import CommitmentRepo
from storage.repositories.conversation_repo import ConversationRepo
from storage.repositories.intervention_repo import InterventionRepo
from storage.repositories.turn_repo import TurnRepo


class SqlitePersistSink(PersistPort):
    """Persist a Brain PersistHandoff into Vault SQLite tables."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conversations = ConversationRepo(conn)
        self._turns = TurnRepo(conn)
        self._commitments = CommitmentRepo(conn)
        self._interventions = InterventionRepo(conn)

    def persist(self, handoff: PersistHandoff) -> None:
        conversation = self._conversations.ensure_for_session(
            session_id=handoff.session_id,
            created_at=handoff.created_at,
            conversation_id=handoff.conversation_id,
        )
        conversation_id = conversation.conversation_id

        # Idempotent turn append.
        if self._turns.get(handoff.turn_id) is None:
            self._turns.create(
                turn_id=handoff.turn_id,
                conversation_id=conversation_id,
                speaker_role=handoff.speaker_role,
                content=handoff.turn_text,
                created_at=handoff.created_at,
            )

        commitment_id: Optional[str] = None
        if handoff.commitment is not None:
            commitment_id = self._upsert_commitment(
                handoff, conversation_id=conversation_id
            )

        if handoff.intervened_this_turn and commitment_id:
            reason = handoff.intervention_reason
            if not reason:
                parts = list(handoff.policy_notes)
                if handoff.speech_action:
                    parts.append(f"speech_action={handoff.speech_action}")
                reason = "; ".join(parts) if parts else None
            self._interventions.create(
                commitment_id=commitment_id,
                source_turn_ids=[handoff.turn_id],
                created_at=handoff.created_at,
                reason=reason,
            )

        # skipped_by_filter with no commitment: conversation + turn already done.

    def _upsert_commitment(
        self,
        handoff: PersistHandoff,
        *,
        conversation_id: str,
    ) -> str:
        assert handoff.commitment is not None
        cdict = dict(handoff.commitment)

        commitment_id = str(cdict["commitment_id"])
        source_turn_ids = list(cdict.get("source_turn_ids") or [])
        if not source_turn_ids:
            source_turn_ids = [handoff.turn_id]
            cdict["source_turn_ids"] = source_turn_ids

        # Prefer handoff session_id; keep Brain session from dict if set.
        session_id = handoff.session_id or cdict.get("session_id")
        cdict["session_id"] = session_id

        status = cdict.get("status")
        if hasattr(status, "value"):
            status = status.value
        status_str = str(status) if status is not None else handoff.transition.to_status

        existing = self._commitments.get(commitment_id)
        if existing is None:
            # Create at the committed status. create() already appends
            # null → status event; do not double-write an identical transition.
            kwargs = self._create_kwargs(cdict, status_str=status_str)
            kwargs["conversation_id"] = conversation_id
            kwargs["session_id"] = session_id
            kwargs["source_turn_ids"] = source_turn_ids
            # Timestamps: prefer commitment dict, fall back to handoff.
            kwargs.setdefault("created_at", handoff.created_at)
            kwargs.setdefault("updated_at", handoff.created_at)
            self._commitments.create(**kwargs)
            return commitment_id

        # Existing row: refresh metadata, then transition if status differs.
        self._commitments.update_fields(
            commitment_id,
            topic_id=cdict.get("topic_id"),
            speaker_role=cdict.get("speaker_role"),
            canonical_text=cdict.get("canonical_text"),
            raw_span=cdict.get("raw_span"),
            source_turn_ids=source_turn_ids,
            clarification_count=cdict.get("clarification_count"),
            intervened=cdict.get("intervened"),
            is_acknowledgement=cdict.get("is_acknowledgement"),
            is_intention_only=cdict.get("is_intention_only"),
            confidence=cdict.get("confidence"),
            conditions=cdict.get("conditions"),
            session_id=session_id,
            conversation_id=conversation_id,
            condition=cdict.get("condition"),
            dependency_owner=cdict.get("dependency_owner"),
            condition_status=cdict.get("condition_status"),
            due_at=cdict.get("due_at"),
            next_followup_at=cdict.get("next_followup_at"),
            superseded_by_commitment_id=cdict.get("superseded_by_commitment_id"),
            updated_at=cdict.get("updated_at") or handoff.created_at,
        )

        to_status = handoff.transition.to_status or status_str
        if existing.status != to_status:
            event_turn_ids = source_turn_ids or [handoff.turn_id]
            self._commitments.update_status(
                commitment_id,
                to_status,
                source_turn_ids=event_turn_ids,
                updated_at=cdict.get("updated_at") or handoff.created_at,
                note="persist_handoff",
                next_followup_at=cdict.get("next_followup_at"),
                due_at=cdict.get("due_at"),
                condition_status=cdict.get("condition_status"),
                superseded_by_commitment_id=cdict.get("superseded_by_commitment_id"),
                intervened=cdict.get("intervened"),
                clarification_count=cdict.get("clarification_count"),
            )

        return commitment_id

    @staticmethod
    def _create_kwargs(cdict: dict[str, Any], *, status_str: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "commitment_id": str(cdict["commitment_id"]),
            "topic_id": str(cdict.get("topic_id") or ""),
            "status": status_str,
            "speaker_role": str(cdict.get("speaker_role") or ""),
            "canonical_text": str(cdict.get("canonical_text") or ""),
            "source_turn_ids": list(cdict.get("source_turn_ids") or []),
            "created_at": str(cdict["created_at"]) if "created_at" in cdict else None,
            "updated_at": str(cdict["updated_at"]) if "updated_at" in cdict else None,
        }
        # Optional Brain + Vault fields — only pass when present so defaults apply.
        optional_map = {
            "raw_span": cdict.get("raw_span"),
            "clarification_count": cdict.get("clarification_count"),
            "intervened": cdict.get("intervened"),
            "is_acknowledgement": cdict.get("is_acknowledgement"),
            "is_intention_only": cdict.get("is_intention_only"),
            "confidence": cdict.get("confidence"),
            "conditions": cdict.get("conditions"),
            "session_id": cdict.get("session_id"),
            "condition": cdict.get("condition"),
            "dependency_owner": cdict.get("dependency_owner"),
            "condition_status": (
                cdict["condition_status"].value
                if hasattr(cdict.get("condition_status"), "value")
                else cdict.get("condition_status")
            ),
            "due_at": cdict.get("due_at"),
            "next_followup_at": cdict.get("next_followup_at"),
            "superseded_by_commitment_id": cdict.get("superseded_by_commitment_id"),
        }
        for key, value in optional_map.items():
            if value is not None:
                kwargs[key] = value
        # Drop None created_at/updated_at so caller can setdefault.
        return {k: v for k, v in kwargs.items() if v is not None or k in (
            "commitment_id", "topic_id", "status", "speaker_role",
            "canonical_text", "source_turn_ids",
        )}


# Brain wiring example (do not import storage from intelligence/):
#
#   from shared.persist_handoff import NullPersistPort, PersistPort, build_handoff
#   # Vault injects SqlitePersistSink at process start; Brain default is NullPersistPort.
#   persist_port: PersistPort = NullPersistPort()
#   ...
#   result = pipeline.handle_turn(incoming)
#   persist_port.persist(build_handoff(
#       session_id=incoming.session_id,
#       turn_id=incoming.turn_id,
#       speaker_role=incoming.speaker_role,
#       turn_text=incoming.text,
#       created_at=incoming.created_at,
#       speech_action=result.speech_action,
#       from_status=prior_status,  # None on first create
#       to_status=(result.commitment.status.value if result.commitment else "NO_COMMITMENT"),
#       clarification_question=result.clarification_question,
#       skipped_by_filter=result.skipped_by_filter,
#       policy_notes=result.policy_notes,
#       commitment=result.commitment.to_dict() if result.commitment else None,
#       intervened_this_turn=bool(result.commitment and result.commitment.intervened),
#   ))

