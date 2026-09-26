"""Golden-dialog evaluation for VeraLock Brain.

Run ``pytest evaluation/`` or ``python -m evaluation``. Fixture mode uses
StubLLM and does not read API keys.
"""

from evaluation.harness import evaluate, load_scenarios, run_scenario
from evaluation.schema import Scenario, ScenarioTurn, TurnExpect

__all__ = [
    "Scenario",
    "ScenarioTurn",
    "TurnExpect",
    "evaluate",
    "load_scenarios",
    "run_scenario",
]
