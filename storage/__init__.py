"""Vault persistence: SQLite schema, models, repositories, and persist sink."""

from storage.db import connect, init_schema

__all__ = ["connect", "init_schema"]
