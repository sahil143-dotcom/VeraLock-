"""Follow-up message templates (no LLM)."""

from __future__ import annotations

from typing import Optional

from storage.models import CommitmentRow


def render_followup_message(commitment: CommitmentRow, *, overdue: bool = False) -> str:
    """Build a deterministic follow-up reminder from commitment fields."""
    who = commitment.speaker_role or "party"
    text = commitment.canonical_text.strip()
    topic = commitment.topic_id

    if overdue:
        header = f"[OVERDUE] Follow-up on commitment ({topic})"
    else:
        header = f"[REMINDER] Follow-up on commitment ({topic})"

    lines = [
        header,
        f"Speaker: {who}",
        f"Commitment: {text}",
        f"Status: {commitment.status}",
    ]
    if commitment.due_at:
        lines.append(f"Due at: {commitment.due_at}")
    if commitment.condition:
        cs = commitment.condition_status or "UNKNOWN"
        lines.append(f"Condition ({cs}): {commitment.condition}")
        if commitment.dependency_owner:
            lines.append(f"Dependency owner: {commitment.dependency_owner}")
    if commitment.next_followup_at:
        lines.append(f"Scheduled follow-up: {commitment.next_followup_at}")

    return "\n".join(lines)


def render_clarification_nudge(
    commitment: CommitmentRow,
    question: Optional[str] = None,
) -> str:
    """Template nudge when status is AWAITING_CLARIFICATION."""
    q = question or "Can you confirm this commitment, or clarify the conditions?"
    return (
        f"[CLARIFICATION] Re: {commitment.topic_id}\n"
        f"Heard: {commitment.canonical_text}\n"
        f"Question: {q}"
    )
