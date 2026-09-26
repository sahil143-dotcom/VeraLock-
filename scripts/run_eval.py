#!/usr/bin/env python3
"""Run every golden dialog and print a pass/fail table.

From the repo root:

    python scripts/run_eval.py

Columns are scenario id, turn, and expected vs actual speech_action and
status. A skipped turn with no commitment shows status ``none``. Exit
status is non-zero when any turn fails. Pytest discovery is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evaluation.harness import evaluate, format_turn_table


def main() -> int:
    report = evaluate()
    print(format_turn_table(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
