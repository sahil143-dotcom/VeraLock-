"""Rolling conversation window for one session.

Keeps the last 6 turns, every commitment seen this session, and the speaker
roles that appear in either. The reasoner snapshot includes only active
commitments (DETECTED, AWAITING_CLARIFICATION, CONFIRMED).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from shared.commitment_schema import Commitment, CommitmentStatus

MAX_TURNS = 6

ACTIVE_STATUSES = frozenset(
    {
        CommitmentStatus.DETECTED,
        CommitmentStatus.AWAITING_CLARIFICATION,
        CommitmentStatus.CONFIRMED,
    }
)


def as_status(value: CommitmentStatus | str) -> CommitmentStatus:
    if isinstance(value, CommitmentStatus):
        return value
    return CommitmentStatus(value)


@dataclass
class Turn:
    turn_id: str
    speaker_role: str
    text: str
    created_at: str


@dataclass
class ContextSnapshot:
    session_id: str
    turns: list[Turn]
    active_commitments: list[Commitment]
    speaker_roles: list[str]


@dataclass
class ContextWindow:
    session_id: str
    max_turns: int = MAX_TURNS
    _turns: deque[Turn] = field(init=False)
    _commitments: dict[str, Commitment] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._turns = deque(maxlen=self.max_turns)

    def add_turn(self, turn: Turn) -> None:
        self._turns.append(turn)

    def upsert(self, commitment: Commitment) -> None:
        self._commitments[commitment.commitment_id] = commitment

    def turns(self) -> list[Turn]:
        return list(self._turns)

    def commitments(self) -> list[Commitment]:
        return list(self._commitments.values())

    def active_commitments(self) -> list[Commitment]:
        rows = [c for c in self._commitments.values() if as_status(c.status) in ACTIVE_STATUSES]
        rows.sort(key=lambda c: (c.updated_at, c.commitment_id))
        return rows

    def for_topic(self, topic_id: str, speaker_role: str) -> list[Commitment]:
        rows = [
            c
            for c in self._commitments.values()
            if c.topic_id == topic_id and c.speaker_role == speaker_role
        ]
        rows.sort(key=lambda c: (c.updated_at, c.commitment_id))
        return rows

    def speaker_roles(self) -> list[str]:
        roles: list[str] = []
        for turn in self._turns:
            if turn.speaker_role not in roles:
                roles.append(turn.speaker_role)
        for commitment in self.active_commitments():
            if commitment.speaker_role not in roles:
                roles.append(commitment.speaker_role)
        return roles

    def snapshot(self) -> ContextSnapshot:
        return ContextSnapshot(
            session_id=self.session_id,
            turns=self.turns(),
            active_commitments=self.active_commitments(),
            speaker_roles=self.speaker_roles(),
        )
