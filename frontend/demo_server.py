#!/usr/bin/env python3
"""VeraLock local demo: fixture Brain, Vault SQLite, and a static UI.

This process is the only place that wires Brain to Vault. It does not modify
``intelligence/``. ``create_app(persist=...)`` is used because that argument
already exists on this branch; ``BrainPipeline`` then builds the shared
``PersistHandoff`` (including ``from_status``) and calls ``SqlitePersistSink``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evidence.provenance import format_evidence_chain, get_commitment_evidence
from intelligence.api import create_app
from shared.persist_handoff import PersistHandoff, PersistPort
from storage.db import connect, init_schema
from storage.models import CommitmentRow
from storage.persist_sink import SqlitePersistSink
from storage.repositories.conversation_repo import ConversationRepo
from storage.repositories.turn_repo import TurnRepo

_FRONTEND_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _FRONTEND_DIR / "static"
DEFAULT_DB_PATH = _FRONTEND_DIR / "demo.sqlite"

# One connection is shared with uvicorn's sync-endpoint threadpool.
_db_lock = threading.Lock()


class LockedPersistSink(PersistPort):
    """Serialize Vault writes. The underlying connection is not thread-safe."""

    def __init__(self, inner: SqlitePersistSink, lock: threading.Lock) -> None:
        self._inner = inner
        self._lock = lock

    def persist(self, handoff: PersistHandoff) -> None:
        with self._lock:
            self._inner.persist(handoff)


def open_demo_db(db_path: str | Path) -> sqlite3.Connection:
    """Open a demo SQLite file via ``storage.db.connect`` and apply the schema.

    ``storage.db.connect`` leaves SQLite's same-thread check on. Sync FastAPI
    routes run on a worker thread, so the demo connection is opened with
    ``check_same_thread=False`` and every use takes ``_db_lock``.
    """
    path = Path(db_path)
    if path != Path(":memory:"):
        path.parent.mkdir(parents=True, exist_ok=True)

    real_connect = sqlite3.connect

    def _connect(database: str, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("check_same_thread", False)
        return real_connect(database, *args, **kwargs)

    sqlite3.connect = _connect  # type: ignore[assignment]
    try:
        conn = connect(path)
    finally:
        sqlite3.connect = real_connect  # type: ignore[assignment]

    init_schema(conn)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.commit()
    return conn


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _commitments_for_session(conn: sqlite3.Connection, session_id: str) -> list[CommitmentRow]:
    cur = conn.execute(
        """
        SELECT * FROM commitments
        WHERE session_id = ?
        ORDER BY updated_at ASC, commitment_id ASC
        """,
        (session_id,),
    )
    return [CommitmentRow.from_row(row) for row in cur.fetchall()]


def create_demo_app(db_path: str | Path | None = None):
    """Build the demo ASGI app: Brain ``POST /v1/turn`` plus UI and evidence."""
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = open_demo_db(path)
    sink = LockedPersistSink(SqlitePersistSink(conn), _db_lock)
    # fixture_mode=True inside create_app when no pipeline is passed.
    app: FastAPI = create_app(persist=sink)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/v1/demo/status")
    def demo_status() -> dict[str, Any]:
        return {
            "status": "ok",
            "component": "demo",
            "fixture_mode": True,
            "persist": "SqlitePersistSink",
            "db_path": str(path),
        }

    @app.get("/v1/evidence/{commitment_id}")
    def evidence(commitment_id: str) -> dict[str, Any]:
        try:
            with _db_lock:
                payload = get_commitment_evidence(commitment_id, conn)
                chain = format_evidence_chain(payload)
        except KeyError:
            return {
                "available": False,
                "commitment_id": commitment_id,
                "detail": "No Vault row for this commitment yet.",
            }
        body = _jsonable(payload)
        body["available"] = True
        body["commitment_id"] = commitment_id
        body["chain"] = chain
        return body

    @app.get("/v1/sessions/{session_id}")
    def session_snapshot(session_id: str) -> dict[str, Any]:
        with _db_lock:
            conversation = ConversationRepo(conn).get_by_session_id(session_id)
            if conversation is None:
                turns: list[Any] = []
                commitments: list[CommitmentRow] = []
                conversation_id = None
            else:
                turns = TurnRepo(conn).list_for_conversation(conversation.conversation_id)
                commitments = _commitments_for_session(conn, session_id)
                conversation_id = conversation.conversation_id
        return {
            "session_id": session_id,
            "conversation_id": conversation_id,
            "turn_count": len(turns),
            "commitment_count": len(commitments),
            "turns": _jsonable(turns),
            "commitments": _jsonable(commitments),
        }

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.state.db_conn = conn
    app.state.db_path = str(path)
    app.state.db_lock = _db_lock
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the VeraLock local demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite file for Vault rows (default: frontend/demo.sqlite)",
    )
    args = parser.parse_args()

    app = create_demo_app(args.db)
    url = f"http://{args.host}:{args.port}"
    print(f"VeraLock demo  {url}")
    print(f"fixture_mode   on (no API keys)")
    print(f"sqlite         {Path(args.db).resolve()}")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
