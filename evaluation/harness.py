"""Load golden dialogs, run BrainPipeline in fixture mode, and score them.

One pipeline instance per scenario keeps session state (clarification ledger
and active commitments) the same way the multi-turn policy tests do. Callers
do not pass ``active_commitments`` between turns.

PersistHandoff end-to-end is intentionally not scored here. Pass a
``pipeline_factory`` that supplies a ``PersistPort`` and a ``TurnObserver``
that reads what that port recorded. Golden tests use neither.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, Sequence

from intelligence.models import TurnInput, TurnResult
from intelligence.pipeline import BrainPipeline

from evaluation.schema import (
    Scenario,
    ScenarioFormatError,
    ScenarioTurn,
    parse_scenario,
)

SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"

PipelineFactory = Callable[[Scenario], BrainPipeline]


class TurnObserver(Protocol):
    """Extra checks after ``handle_turn``. Return failure messages.

    A later Brain+Vault suite can implement this against
    ``RecordingPersistSink.handoffs`` or ``SqlitePersistSink`` without
    changing the golden dialog files. An empty list means the observer passed.
    """

    def after_turn(
        self,
        scenario: Scenario,
        turn: ScenarioTurn,
        pipeline: BrainPipeline,
        result: TurnResult,
    ) -> list[str]:
        """Score one finished turn. Return human-readable failures."""


@dataclass(frozen=True)
class Failure:
    scenario_id: str
    turn_id: Optional[str]
    message: str


@dataclass
class TurnScore:
    turn_id: str
    passed: bool
    failures: list[Failure] = field(default_factory=list)


@dataclass
class ScenarioResult:
    scenario_id: str
    title: str
    passed: bool
    failures: list[Failure]
    turns: list[TurnScore]

    def render(self) -> str:
        lines = [
            f"{'PASS' if self.passed else 'FAIL'} {self.scenario_id}: {self.title}"
        ]
        for failure in self.failures:
            lines.append(f"  - {failure.message}")
        return "\n".join(lines)


@dataclass
class EvalReport:
    results: list[ScenarioResult]

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(result.passed for result in self.results)

    def render(self) -> str:
        body = "\n".join(result.render() for result in self.results)
        passed = sum(1 for result in self.results if result.passed)
        summary = f"{passed}/{len(self.results)} scenarios passed"
        return f"{body}\n{summary}" if body else summary


def fixture_pipeline(_scenario: Scenario) -> BrainPipeline:
    """Default runner. StubLLM, no network, no API key, no persist port."""
    return BrainPipeline(fixture_mode=True)


def load_scenario(path: Path) -> Scenario:
    data = _decode(path)
    return parse_scenario(data, source=str(path))


def load_scenarios(directory: Optional[Path] = None) -> list[Scenario]:
    root = directory or SCENARIOS_DIR
    paths = sorted(
        [*root.glob("*.yaml"), *root.glob("*.yml"), *root.glob("*.json")]
    )
    scenarios = [load_scenario(path) for path in paths]
    seen: dict[str, str] = {}
    for scenario in scenarios:
        previous = seen.get(scenario.id)
        if previous is not None:
            raise ScenarioFormatError(
                f"duplicate scenario id {scenario.id!r} in {previous} and {scenario.source}"
            )
        seen[scenario.id] = scenario.source
    return scenarios


def run_scenario(
    scenario: Scenario,
    *,
    pipeline_factory: Optional[PipelineFactory] = None,
    observers: Sequence[TurnObserver] = (),
) -> ScenarioResult:
    factory = pipeline_factory or fixture_pipeline
    pipeline = factory(scenario)
    failures: list[Failure] = []
    scores: list[TurnScore] = []
    commitment_ids: dict[str, Optional[str]] = {}

    for turn in scenario.turns:
        turn_failures: list[Failure] = []
        try:
            result = pipeline.handle_turn(_turn_input(scenario, turn))
        except Exception as exc:
            turn_failures.append(
                Failure(
                    scenario.id,
                    turn.turn_id,
                    f"{scenario.id} {turn.turn_id}: handle_turn raised "
                    f"{type(exc).__name__}: {exc}",
                )
            )
            failures.extend(turn_failures)
            scores.append(
                TurnScore(turn_id=turn.turn_id, passed=False, failures=turn_failures)
            )
            break

        commitment_ids[turn.turn_id] = (
            result.commitment.commitment_id if result.commitment is not None else None
        )
        turn_failures.extend(_score_turn(scenario, turn, result, commitment_ids))
        for observer in observers:
            for message in observer.after_turn(scenario, turn, pipeline, result):
                turn_failures.append(Failure(scenario.id, turn.turn_id, message))
        failures.extend(turn_failures)
        scores.append(
            TurnScore(
                turn_id=turn.turn_id,
                passed=not turn_failures,
                failures=list(turn_failures),
            )
        )

    return ScenarioResult(
        scenario_id=scenario.id,
        title=scenario.title,
        passed=not failures,
        failures=failures,
        turns=scores,
    )


def evaluate(
    directory: Optional[Path] = None,
    *,
    pipeline_factory: Optional[PipelineFactory] = None,
    observers: Sequence[TurnObserver] = (),
) -> EvalReport:
    results = [
        run_scenario(
            scenario,
            pipeline_factory=pipeline_factory,
            observers=observers,
        )
        for scenario in load_scenarios(directory)
    ]
    return EvalReport(results=results)


def _decode(path: Path) -> object:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ScenarioFormatError(f"{path}: invalid JSON: {exc}") from exc
    if suffix not in {".yaml", ".yml"}:
        raise ScenarioFormatError(f"{path}: expected .yaml, .yml, or .json")
    try:
        import yaml
    except ImportError as exc:
        raise ScenarioFormatError(
            "PyYAML is required to load golden dialogs. "
            "Install the dev extra: pip install pyyaml"
        ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioFormatError(f"{path}: invalid YAML: {exc}") from exc


def _turn_input(scenario: Scenario, turn: ScenarioTurn) -> TurnInput:
    return TurnInput(
        session_id=scenario.session_id,
        turn_id=turn.turn_id,
        speaker_role=turn.speaker_role,
        text=turn.text,
        created_at=turn.created_at,
        intervened=turn.intervened,
    )


def _score_turn(
    scenario: Scenario,
    turn: ScenarioTurn,
    result: TurnResult,
    commitment_ids: dict[str, Optional[str]],
) -> list[Failure]:
    expect = turn.expect
    failures: list[Failure] = []
    commitment = result.commitment
    status = None
    if commitment is not None:
        raw_status = commitment.status
        status = raw_status.value if hasattr(raw_status, "value") else str(raw_status)

    def mismatch(field_name: str, expected: object, actual: object) -> None:
        failures.append(
            Failure(
                scenario.id,
                turn.turn_id,
                f"{scenario.id} {turn.turn_id}: {field_name} expected {expected!r}, "
                f"got {actual!r}",
            )
        )

    if result.speech_action != expect.speech_action:
        mismatch("speech_action", expect.speech_action, result.speech_action)

    if expect.status is not None and status != expect.status:
        if commitment is None:
            failures.append(
                Failure(
                    scenario.id,
                    turn.turn_id,
                    f"{scenario.id} {turn.turn_id}: status expected {expect.status!r}, "
                    f"got null commitment (skipped_by_filter={result.skipped_by_filter}, "
                    f"filter_reason={result.filter_reason!r})",
                )
            )
        else:
            mismatch("status", expect.status, status)

    if expect.is_acknowledgement is not None:
        actual = commitment.is_acknowledgement if commitment is not None else None
        if actual != expect.is_acknowledgement:
            mismatch("is_acknowledgement", expect.is_acknowledgement, actual)

    if expect.is_intention_only is not None:
        actual = commitment.is_intention_only if commitment is not None else None
        if actual != expect.is_intention_only:
            mismatch("is_intention_only", expect.is_intention_only, actual)

    if expect.intervened is not None:
        actual = commitment.intervened if commitment is not None else None
        if actual != expect.intervened:
            mismatch("intervened", expect.intervened, actual)

    if expect.check_clarification_question:
        if result.clarification_question != expect.clarification_question:
            mismatch(
                "clarification_question",
                expect.clarification_question,
                result.clarification_question,
            )

    if expect.clarification_question_contains is not None:
        question = result.clarification_question or ""
        if expect.clarification_question_contains not in question:
            mismatch(
                "clarification_question_contains",
                expect.clarification_question_contains,
                result.clarification_question,
            )

    if expect.same_commitment_as is not None:
        previous_id = commitment_ids.get(expect.same_commitment_as)
        current_id = commitment.commitment_id if commitment is not None else None
        if previous_id is None or current_id is None or previous_id != current_id:
            failures.append(
                Failure(
                    scenario.id,
                    turn.turn_id,
                    f"{scenario.id} {turn.turn_id}: same_commitment_as "
                    f"{expect.same_commitment_as} expected commitment_id "
                    f"{previous_id!r}, got {current_id!r}",
                )
            )

    return failures
