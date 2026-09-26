"""Exit non-zero when any golden dialog fails."""

from __future__ import annotations

from evaluation.harness import evaluate, format_turn_table


def main() -> int:
    report = evaluate()
    print(format_turn_table(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
