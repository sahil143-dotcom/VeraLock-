"""Legal commitment status transitions.

Rejects anything outside the graph. Policy (acknowledgement, intention,
intervention, clarification cap) lives in `intelligence/guardrails.py`;
this module only checks that the requested hop is legal.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from shared.commitment_schema import Commitment, CommitmentStatus

# Entry statuses a brand-new record may take. SUPERSEDED and WITHDRAWN require
# a prior commitment.
_INITIAL = frozenset(
    {
        CommitmentStatus.NO_COMMITMENT,
        CommitmentStatus.DETECTED,
        CommitmentStatus.AWAITING_CLARIFICATION,
        CommitmentStatus.CONFIRMED,
        CommitmentStatus.UNRESOLVED_AMBIGUOUS,
    }
)

_OPEN_EXITS = frozenset(
    {
        CommitmentStatus.NO_COMMITMENT,
        CommitmentStatus.AWAITING_CLARIFICATION,
        CommitmentStatus.CONFIRMED,
        CommitmentStatus.UNRESOLVED_AMBIGUOUS,
        CommitmentStatus.WITHDRAWN,
        CommitmentStatus.SUPERSEDED,
    }
)

ALLOWED: dict[Optional[CommitmentStatus], frozenset[CommitmentStatus]] = {
    None: _INITIAL,
    CommitmentStatus.DETECTED: _OPEN_EXITS,
    CommitmentStatus.AWAITING_CLARIFICATION: frozenset(
        {
            CommitmentStatus.CONFIRMED,
            CommitmentStatus.UNRESOLVED_AMBIGUOUS,
            CommitmentStatus.NO_COMMITMENT,
            CommitmentStatus.WITHDRAWN,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.CONFIRMED: frozenset(
        {
            CommitmentStatus.WITHDRAWN,
            CommitmentStatus.SUPERSEDED,
        }
    ),
    CommitmentStatus.NO_COMMITMENT: frozenset(),
    CommitmentStatus.UNRESOLVED_AMBIGUOUS: frozenset(),
    CommitmentStatus.SUPERSEDED: frozenset(),
    CommitmentStatus.WITHDRAWN: frozenset(),
}


class IllegalTransitionError(Exception):
    """The requested status change is not in the commitment graph."""

    def __init__(
        self,
        from_status: Optional[CommitmentStatus],
        to_status: CommitmentStatus,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        src = from_status.value if from_status is not None else "NONE"
        super().__init__(f"illegal commitment transition: {src} -> {to_status.value}")


def _coerce(status: CommitmentStatus | str | None) -> Optional[CommitmentStatus]:
    if status is None:
        return None
    if isinstance(status, CommitmentStatus):
        return status
    return CommitmentStatus(status)


class CommitmentMachine:
    def allowed(
        self,
        from_status: CommitmentStatus | str | None,
        to_status: CommitmentStatus | str,
    ) -> bool:
        src = _coerce(from_status)
        dst = _coerce(to_status)
        assert dst is not None
        if src is not None and src == dst:
            return True
        return dst in ALLOWED[src]

    def create(self, commitment: Commitment) -> Commitment:
        status = _coerce(commitment.status)
        assert status is not None
        if status not in ALLOWED[None]:
            raise IllegalTransitionError(None, status)
        if commitment.status is not status:
            return replace(commitment, status=status)
        return commitment

    def transition(
        self,
        commitment: Commitment,
        to_status: CommitmentStatus | str,
        *,
        updated_at: str,
        source_turn_ids: Optional[list[str]] = None,
        **fields: Any,
    ) -> Commitment:
        """Move `commitment` to `to_status` or refresh fields when status is unchanged.

        Same-status updates are bookkeeping (flags, source turns), not new hops.
        """
        current = _coerce(commitment.status)
        dest = _coerce(to_status)
        assert current is not None and dest is not None
        updates = dict(fields)
        updates["status"] = dest
        updates["updated_at"] = updated_at
        if source_turn_ids is not None:
            updates["source_turn_ids"] = list(source_turn_ids)
        if current == dest:
            return replace(commitment, **updates)
        if dest not in ALLOWED[current]:
            raise IllegalTransitionError(current, dest)
        return replace(commitment, **updates)
