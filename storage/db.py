"""SQLite connection and schema bootstrap for Vault."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a SQLite connection with foreign keys and row factory."""
    path = str(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply schema.sql DDL to the given connection."""
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(ddl)
    conn.commit()
