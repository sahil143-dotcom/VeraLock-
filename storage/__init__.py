"""Vault persistence: SQLite schema, models, and repositories."""

from storage.db import connect, init_schema

__all__ = ["connect", "init_schema"]
