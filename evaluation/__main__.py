"""Exit non-zero when any golden dialog fails."""

from __future__ import annotations

from evaluation.harness import evaluate


def main() -> int:
    report = evaluate()
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
