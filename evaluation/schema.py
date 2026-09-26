"""Scenario dataclasses and YAML/JSON parsing for golden dialogs.

A scenario is one stateful session: the harness runs every turn through a
single BrainPipeline. Expectations are per turn and only the fields present
in ``expect`` are scored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from shared.commitment_schema import CommitmentStatus

SPEECH_ACTIONS = frozenset({"SILENT", "CLARIFY"})

_SCENARIO_KEYS = frozenset({"id", "title", "description", "session_id", "turns"})
_TURN_KEYS = frozenset(
    {"turn_id", "speaker_role", "text", "created_at", "intervened", "expect"}
)
_EXPECT_KEYS = frozenset(
    {
        "speech_action",
        "status",
        "is_acknowledgement",
        "is_intention_only",
        "clarification_question",
        "clarification_question_contains",
        "clarification_count",
        "intervened",
        "same_commitment_as",
        "skipped_by_filter",
        "filter_reason",
    }
)


class ScenarioFormatError(ValueError):
    """A scenario file does not match the golden-dialog schema."""


@dataclass(frozen=True)
class TurnExpect:
    """Fields to compare against one TurnResult.

    ``check_clarification_question`` is true when the file set
    ``clarification_question`` (including null). A missing key is not scored.
    """

    speech_action: str
    status: Optional[str] = None
    check_status: bool = False
    is_acknowledgement: Optional[bool] = None
    is_intention_only: Optional[bool] = None
    clarification_question: Optional[str] = None
    check_clarification_question: bool = False
    clarification_question_contains: Optional[str] = None
    clarification_count: Optional[int] = None
    intervened: Optional[bool] = None
    same_commitment_as: Optional[str] = None
    skipped_by_filter: Optional[bool] = None
    filter_reason: Optional[str] = None


@dataclass(frozen=True)
class ScenarioTurn:
    turn_id: str
    speaker_role: str
    text: str
    created_at: str
    intervened: bool
    expect: TurnExpect


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    description: str
    session_id: str
    turns: tuple[ScenarioTurn, ...]
    source: str


def parse_scenario(data: Any, *, source: str) -> Scenario:
    """Build a Scenario from a decoded YAML or JSON object."""
    if not isinstance(data, dict):
        raise ScenarioFormatError(f"{source}: scenario must be a mapping")
    unknown = set(data) - _SCENARIO_KEYS
    if unknown:
        raise ScenarioFormatError(
            f"{source}: unknown scenario fields {sorted(unknown)}"
        )
    scenario_id = _require_str(data, "id", source)
    title = _require_str(data, "title", source)
    description = _require_str(data, "description", source)
    session_id = _require_str(data, "session_id", source)
    raw_turns = data.get("turns")
    if not isinstance(raw_turns, list) or not raw_turns:
        raise ScenarioFormatError(f"{source}: turns must be a non-empty list")

    turns: list[ScenarioTurn] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_turns):
        turn = _parse_turn(raw, source=source, index=index)
        if turn.turn_id in seen:
            raise ScenarioFormatError(
                f"{source}: duplicate turn_id {turn.turn_id!r}"
            )
        ref = turn.expect.same_commitment_as
        if ref is not None and ref not in seen:
            raise ScenarioFormatError(
                f"{source}: {turn.turn_id} same_commitment_as {ref!r} "
                "must be an earlier turn_id"
            )
        seen.add(turn.turn_id)
        turns.append(turn)

    return Scenario(
        id=scenario_id,
        title=title,
        description=description,
        session_id=session_id,
        turns=tuple(turns),
        source=source,
    )


def _parse_turn(raw: Any, *, source: str, index: int) -> ScenarioTurn:
    where = f"{source} turns[{index}]"
    if not isinstance(raw, dict):
        raise ScenarioFormatError(f"{where}: turn must be a mapping")
    unknown = set(raw) - _TURN_KEYS
    if unknown:
        raise ScenarioFormatError(f"{where}: unknown turn fields {sorted(unknown)}")
    turn_id = _require_str(raw, "turn_id", where)
    speaker_role = _require_str(raw, "speaker_role", where)
    text = _require_turn_text(raw, "text", where)
    if "created_at" in raw and raw["created_at"] is not None:
        created_at = _as_str(raw["created_at"], f"{where}.created_at")
    else:
        created_at = _default_created_at(index)
    intervened = False
    if "intervened" in raw and raw["intervened"] is not None:
        intervened = _as_bool(raw["intervened"], f"{where}.intervened")
    if "expect" not in raw:
        raise ScenarioFormatError(f"{where}: missing expect")
    expect = _parse_expect(raw["expect"], where=f"{where}.expect")
    return ScenarioTurn(
        turn_id=turn_id,
        speaker_role=speaker_role,
        text=text,
        created_at=created_at,
        intervened=intervened,
        expect=expect,
    )


def _parse_expect(raw: Any, *, where: str) -> TurnExpect:
    if not isinstance(raw, dict):
        raise ScenarioFormatError(f"{where}: expect must be a mapping")
    unknown = set(raw) - _EXPECT_KEYS
    if unknown:
        raise ScenarioFormatError(f"{where}: unknown expect fields {sorted(unknown)}")
    speech = _require_str(raw, "speech_action", where)
    if speech not in SPEECH_ACTIONS:
        raise ScenarioFormatError(
            f"{where}.speech_action must be SILENT or CLARIFY, got {speech!r}"
        )
    check_status = "status" in raw
    status: Optional[str] = None
    if check_status and raw["status"] is not None:
        status = _as_str(raw["status"], f"{where}.status")
        try:
            CommitmentStatus(status)
        except ValueError as exc:
            raise ScenarioFormatError(
                f"{where}.status is not a CommitmentStatus: {status!r}"
            ) from exc
    contains = _optional_str(raw, "clarification_question_contains", where)
    same = _optional_str(raw, "same_commitment_as", where)
    check_question = "clarification_question" in raw
    question: Optional[str]
    if check_question and raw["clarification_question"] is not None:
        question = _as_str(
            raw["clarification_question"], f"{where}.clarification_question"
        )
    else:
        question = None
    return TurnExpect(
        speech_action=speech,
        status=status,
        check_status=check_status,
        is_acknowledgement=_optional_bool(raw, "is_acknowledgement", where),
        is_intention_only=_optional_bool(raw, "is_intention_only", where),
        clarification_question=question,
        check_clarification_question=check_question,
        clarification_question_contains=contains,
        clarification_count=_optional_int(raw, "clarification_count", where),
        intervened=_optional_bool(raw, "intervened", where),
        same_commitment_as=same,
        skipped_by_filter=_optional_bool(raw, "skipped_by_filter", where),
        filter_reason=_optional_str(raw, "filter_reason", where),
    )


def _default_created_at(index: int) -> str:
    minute = index % 60
    hour = index // 60
    return f"2026-09-26T{hour:02d}:{minute:02d}:00Z"


def _require_turn_text(data: dict, key: str, where: str) -> str:
    """Keep the utterance verbatim, including whitespace the cost gate skips."""
    if key not in data or data[key] is None:
        raise ScenarioFormatError(f"{where}: missing {key}")
    value = data[key]
    if not isinstance(value, str):
        raise ScenarioFormatError(
            f"{where}.{key} must be a string, got {type(value).__name__}"
        )
    return value


def _require_str(data: dict, key: str, where: str) -> str:
    if key not in data or data[key] is None:
        raise ScenarioFormatError(f"{where}: missing {key}")
    return _as_str(data[key], f"{where}.{key}")


def _optional_str(data: dict, key: str, where: str) -> Optional[str]:
    if key not in data or data[key] is None:
        return None
    return _as_str(data[key], f"{where}.{key}")


def _optional_bool(data: dict, key: str, where: str) -> Optional[bool]:
    if key not in data or data[key] is None:
        return None
    return _as_bool(data[key], f"{where}.{key}")


def _as_str(value: Any, where: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ScenarioFormatError(f"{where} must be a non-empty string")
        return text
    raise ScenarioFormatError(f"{where} must be a string, got {type(value).__name__}")


def _optional_int(data: dict, key: str, where: str) -> Optional[int]:
    if key not in data or data[key] is None:
        return None
    return _as_int(data[key], f"{where}.{key}")


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioFormatError(f"{where} must be an integer")
    if value < 0:
        raise ScenarioFormatError(f"{where} must be >= 0")
    return value


def _as_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ScenarioFormatError(f"{where} must be a boolean")
