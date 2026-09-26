"""Golden dialogs. No API keys; BrainPipeline fixture mode is deterministic."""

from __future__ import annotations

from pathlib import Path

import pytest

from intelligence.pipeline import BrainPipeline

from evaluation.harness import SCENARIOS_DIR, load_scenario, load_scenarios, run_scenario
from evaluation.schema import Scenario, ScenarioFormatError

REQUIRED_IDS = (
    "acknowledgement",
    "clarification_then_confirm",
    "clarification_then_unresolved",
    "clear_commitment",
    "intention",
    "intervened_blocks_confirm",
)


def _ids() -> list[str]:
    return [scenario.id for scenario in load_scenarios()]


def test_catalog_covers_product_rules():
    ids = _ids()
    assert ids == sorted(ids)
    missing = [scenario_id for scenario_id in REQUIRED_IDS if scenario_id not in ids]
    assert missing == []
    for path in SCENARIOS_DIR.glob("*.yaml"):
        scenario = load_scenario(path)
        assert scenario.id == path.stem
        assert len(scenario.turns) >= 2


@pytest.mark.parametrize("scenario_id", _ids(), ids=_ids())
def test_golden_dialog(scenario_id: str):
    scenario = next(item for item in load_scenarios() if item.id == scenario_id)
    result = run_scenario(scenario)
    assert result.passed, result.render()


def test_harness_reports_a_status_mismatch(tmp_path: Path):
    path = tmp_path / "wrong_status.yaml"
    path.write_text(
        """
id: wrong_status
title: Expectation that Brain will not meet
description: A clear commitment is CONFIRMED, so this NO_COMMITMENT expect fails.
session_id: sess-mismatch
turns:
  - turn_id: turn-1
    speaker_role: user
    text: "I will send the proposal by Friday."
    created_at: "2026-09-26T00:00:00Z"
    expect:
      speech_action: SILENT
      status: NO_COMMITMENT
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = run_scenario(load_scenario(path))
    assert result.passed is False
    rendered = result.render()
    assert "wrong_status turn-1: status expected 'NO_COMMITMENT'" in rendered
    assert "CONFIRMED" in rendered


def test_factory_and_observer_are_the_persist_extension_point():
    """Later PersistHandoff e2e plugs in here without editing the YAML."""
    scenario = next(item for item in load_scenarios() if item.id == "acknowledgement")
    seen: list[str] = []

    def factory(incoming: Scenario) -> BrainPipeline:
        seen.append(incoming.id)
        return BrainPipeline(fixture_mode=True)

    class RecordingObserver:
        def after_turn(self, scenario, turn, pipeline, result):
            del scenario, pipeline
            assert result.speech_action == "SILENT"
            return [f"handoff-check {turn.turn_id}"]

    result = run_scenario(
        scenario,
        pipeline_factory=factory,
        observers=[RecordingObserver()],
    )
    assert seen == ["acknowledgement"]
    assert result.passed is False
    rendered = result.render()
    assert "handoff-check turn-1" in rendered
    assert "handoff-check turn-2" in rendered


def test_unknown_expect_field_is_rejected(tmp_path: Path):
    path = tmp_path / "typo.yaml"
    path.write_text(
        """
id: typo
title: Typo in expect
description: Unknown expect keys fail at load so a misspelled check cannot pass.
session_id: sess-typo
turns:
  - turn_id: turn-1
    speaker_role: user
    text: "Got it."
    expect:
      speech_action: SILENT
      statu: NO_COMMITMENT
""".strip()
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ScenarioFormatError, match="unknown expect fields"):
        load_scenario(path)
