"""Demo time-warp: FAST-FORWARD 48 HOURS override of now()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

# Module-level warp offset applied by get_now().
_warp_delta: timedelta = timedelta(0)


class TimeWarp:
    """Context-style helper to FAST-FORWARD wall clock for demos."""

    FAST_FORWARD_HOURS = 48

    def __init__(self, hours: float = FAST_FORWARD_HOURS) -> None:
        self.hours = hours
        self._previous: Optional[timedelta] = None

    def enable(self) -> "TimeWarp":
        global _warp_delta
        self._previous = _warp_delta
        _warp_delta = timedelta(hours=self.hours)
        return self

    def disable(self) -> None:
        global _warp_delta
        if self._previous is not None:
            _warp_delta = self._previous
            self._previous = None
        else:
            _warp_delta = timedelta(0)

    def __enter__(self) -> "TimeWarp":
        return self.enable()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.disable()

    @staticmethod
    def reset() -> None:
        global _warp_delta
        _warp_delta = timedelta(0)

    @staticmethod
    def current_offset() -> timedelta:
        return _warp_delta


def utc_now_real() -> datetime:
    """True UTC now (timezone-aware)."""
    return datetime.now(timezone.utc)


def get_now() -> datetime:
    """Warped 'now' — real UTC + FAST-FORWARD offset when enabled."""
    return utc_now_real() + _warp_delta


def get_now_iso() -> str:
    """ISO-8601 UTC string for warped now."""
    return get_now().isoformat().replace("+00:00", "Z")


def real_now_iso() -> str:
    return utc_now_real().isoformat().replace("+00:00", "Z")
