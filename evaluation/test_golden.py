"""Golden dialogs. No API keys; BrainPipeline fixture mode is deterministic."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from intelligence.pipeline import BrainPipeline

from evaluation.harness import (
    SCENARIOS_DIR,
    evaluate,
    format_turn_table,
    load_scenario,
    load_scenarios,
    run_scenario,
)
from evaluation.schema import Scenario, ScenarioFormatError

REQUIRED_IDS = (
    "acknowledgement",
    "clarification_then_confirm",
    "clarification_then_unresolved",
    "clear_commitment",
    "filter_skip",
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
    skipped = load_scenario(SCENARIOS_DIR / "filter_skip.yaml")
    assert skipped.turns[0].text == "   "
    assert skipped.turns[0].expect.skipped_by_filter is True
    assert skipped.turns[0].expect.status is None
    assert skipped.turns[0].expect.check_status is True


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


def test_harness_reports_clarification_count_mismatch(tmp_path: Path):
    path = tmp_path / "wrong_count.yaml"
    path.write_text(
        """
id: wrong_count
title: Clarification count that Brain will not meet
description: The first ambiguous budget turn records one clarification, not zero.
session_id: sess-count
turns:
  - turn_id: turn-1
    speaker_role: user
    text: "I'll handle the budget review soon."
    expect:
      speech_action: CLARIFY
      status: AWAITING_CLARIFICATION
      clarification_count: 0
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = run_scenario(load_scenario(path))
    assert result.passed is False
    assert "clarification_count expected 0, got 1" in result.render()


def test_harness_reports_skipped_by_filter_mismatch(tmp_path: Path):
    path = tmp_path / "wrong_skip.yaml"
    path.write_text(
        """
id: wrong_skip
title: Filler that the gate skips
description: um is a filter skip, so expecting a pass fails the harness.
session_id: sess-skip
turns:
  - turn_id: turn-1
    speaker_role: user
    text: "um"
    expect:
      speech_action: SILENT
      status: null
      skipped_by_filter: false
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = run_scenario(load_scenario(path))
    assert result.passed is False
    assert "skipped_by_filter expected False, got True" in result.render()


def test_run_eval_table_lists_every_turn():
    report_text = format_turn_table(evaluate())
    assert "speech_expected" in report_text
    assert "status_actual" in report_text
    assert "filter_skip" in report_text
    assert "turn-empty" in report_text
    assert "none" in report_text
    assert "7/7 scenarios passed" in report_text
    assert [line for line in report_text.splitlines() if line.startswith("FAIL")] == []


def test_run_eval_script_exits_zero():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_eval.py")],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "speech_expected" in completed.stdout
    assert "filter_skip" in completed.stdout
    assert "7/7 scenarios passed" in completed.stdout


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
