"""UTC timestamps for Brain records."""

from __future__ import annotations

from datetime import datetime, timezone


def now_iso() -> str:
    """ISO-8601 UTC timestamp with a Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
