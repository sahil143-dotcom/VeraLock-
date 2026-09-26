"""One clarification question per topic_id.

The ledger is the cap. Guardrails consult it before asking; the pipeline
records a question only when `allow` is true.
"""

from __future__ import annotations

MAX_CLARIFICATIONS_PER_TOPIC = 1


class ClarificationCapExceeded(Exception):
    """Raised when a second question is recorded for the same topic_id."""

    def __init__(self, topic_id: str) -> None:
        self.topic_id = topic_id
        super().__init__(f"clarification cap reached for topic_id={topic_id}")


class ClarificationLedger:
    """Counts are per (session_id, topic_id). Sessions do not share a cap."""

    def __init__(self, max_per_topic: int = MAX_CLARIFICATIONS_PER_TOPIC) -> None:
        self.max_per_topic = max_per_topic
        self._counts: dict[tuple[str, str], int] = {}
        self._questions: dict[tuple[str, str], str] = {}

    def count(self, session_id: str, topic_id: str) -> int:
        return self._counts.get((session_id, topic_id), 0)

    def question(self, session_id: str, topic_id: str) -> str | None:
        return self._questions.get((session_id, topic_id))

    def observe(self, session_id: str, topic_id: str, count: int) -> None:
        """Seed from a commitment supplied by the caller (stateless clients)."""
        if count <= 0:
            return
        key = (session_id, topic_id)
        self._counts[key] = max(self.count(session_id, topic_id), int(count))

    def allow(self, session_id: str, topic_id: str) -> bool:
        return self.count(session_id, topic_id) < self.max_per_topic

    def record(self, session_id: str, topic_id: str, question: str) -> str:
        if not self.allow(session_id, topic_id):
            raise ClarificationCapExceeded(topic_id)
        key = (session_id, topic_id)
        self._counts[key] = self.count(session_id, topic_id) + 1
        self._questions[key] = question
        return question


def build_question(topic_id: str, canonical_text: str) -> str:
    """Deterministic clarification. No model call."""
    heard = canonical_text.strip() or topic_id
    return (
        f'Should I record this as a commitment ({topic_id}): "{heard}"? '
        "Say the exact terms if yes."
    )
