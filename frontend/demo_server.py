#!/usr/bin/env python3
"""VeraLock local demo: fixture Brain, a temp Vault database, and a static UI.

Composition stays in this file. It does not modify ``intelligence/``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evidence.provenance import get_commitment_evidence
from intelligence.api import create_app
from storage.db import connect, init_schema
from storage.models import CommitmentRow
from storage.persist_sink import SqlitePersistSink
from storage.repositories.conversation_repo import ConversationRepo
from storage.repositories.turn_repo import TurnRepo

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def new_temp_db() -> Path:
    """SQLite file under the system temp directory. Removed only by the OS."""
    handle = tempfile.NamedTemporaryFile(
        prefix="veralock-demo-",
        suffix=".sqlite",
        delete=False,
    )
    handle.close()
    return Path(handle.name)


def open_demo_db(db_path: str | Path) -> sqlite3.Connection:
    """Open SQLite with ``storage.db.connect`` and apply ``init_schema``."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path, check_same_thread=False)
    init_schema(conn)
    conn.execute("PRAGMA busy_timeout = 5000")
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
    """Serve the UI and fixture Brain, persisting each turn into SQLite."""
    from fastapi import FastAPI
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    path = Path(db_path) if db_path is not None else new_temp_db()
    conn = open_demo_db(path)
    app: FastAPI = create_app(persist=SqlitePersistSink(conn))

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

    @app.get("/v1/demo/evidence/{commitment_id}")
    def evidence(commitment_id: str) -> dict[str, Any]:
        """Demo-only provenance. Uses the same SQLite connection as the persist sink.

        Missing rows are a soft skip: the turn result still stands, and this
        route does not define a Vault HTTP API.
        """
        try:
            payload = get_commitment_evidence(commitment_id, conn)
        except KeyError:
            return {
                "available": False,
                "commitment_id": commitment_id,
                "detail": "No Vault row for this commitment yet.",
            }
        body = _jsonable(payload)
        body["available"] = True
        body["commitment_id"] = commitment_id
        return body

    @app.get("/v1/sessions/{session_id}")
    def session_snapshot(session_id: str) -> dict[str, Any]:
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
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the VeraLock local demo UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite path (default: a new tempfile)",
    )
    args = parser.parse_args()

    app = create_demo_app(args.db)
    url = f"http://{args.host}:{args.port}"
    print(f"VeraLock demo  {url}")
    print("fixture_mode   on (no API keys)")
    print(f"sqlite         {app.state.db_path}")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
