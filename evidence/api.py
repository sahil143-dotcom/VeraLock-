"""Thin HTTP API exposing get_commitment_evidence for Glass.

Vault-owned. Does not invent tables — reuses evidence.provenance.
FastAPI is an optional dependency (same as intelligence/api.py).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Optional, Union

from evidence.provenance import get_commitment_evidence
from storage.db import connect, init_schema


def _row_to_dict(obj: Any) -> Any:
    """Serialize dataclass rows (and nested lists) to plain dicts for JSON."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, list):
        return [_row_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _row_to_dict(value) for key, value in obj.items()}
    return obj


def evidence_payload(commitment_id: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Fetch evidence and return JSON-ready dicts. Raises KeyError if missing."""
    raw = get_commitment_evidence(commitment_id, conn)
    return {
        "commitment": _row_to_dict(raw["commitment"]),
        "events": _row_to_dict(raw["events"]),
        "intervention_events": _row_to_dict(raw["intervention_events"]),
        "source_turns": _row_to_dict(raw["source_turns"]),
    }


def create_evidence_app(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Union[str, Path]] = None,
):
    """Build a FastAPI app with GET /v1/evidence/{id} and GET /v1/health.

    Pass an open `conn` (preferred for tests; use connect(..., check_same_thread=False)
    when sharing across TestClient worker threads) or a `db_path`. When neither is
    given, uses VERALOCK_VAULT_DB or an in-memory DB (schema applied).
    """
    try:
        from fastapi import FastAPI, HTTPException
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "FastAPI is not installed. Install: pip install fastapi uvicorn"
        ) from exc

    if conn is not None:
        db = conn
    elif db_path is not None:
        db = connect(db_path, check_same_thread=False)
    else:
        path = os.environ.get("VERALOCK_VAULT_DB", ":memory:")
        db = connect(path, check_same_thread=False)
        if str(path) == ":memory:":
            init_schema(db)

    app = FastAPI(title="VeraLock Vault Evidence", version="0.1.0")
    app.state.conn = db

    @app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "vault-evidence"}

    @app.get("/v1/evidence/{commitment_id}")
    def get_evidence(commitment_id: str) -> dict[str, Any]:
        try:
            return evidence_payload(commitment_id, app.state.conn)
        except KeyError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"commitment not found: {commitment_id}",
            ) from exc

    return app


def create_app(
    conn: Optional[sqlite3.Connection] = None,
    db_path: Optional[Union[str, Path]] = None,
):
    """Alias for create_evidence_app (uvicorn --factory friendly)."""
    return create_evidence_app(conn=conn, db_path=db_path)


# Module-level app for `uvicorn evidence.api:app` when VERALOCK_VAULT_DB is set.
# Lazy: only constructed on attribute access so importing the module stays light.
def __getattr__(name: str):
    if name == "app":
        return create_evidence_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
