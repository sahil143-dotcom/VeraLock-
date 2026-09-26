"""FastAPI-compatible text turn entrypoint.

`handle_turn_payload` is framework-free. `create_app` mounts it when FastAPI
is installed. Brain does not import voice code.

Annotations stay eager (no `from __future__ import annotations`) so FastAPI
can see the local request model as a body, not a query parameter.
"""

from typing import Any, Optional

from shared.commitment_schema import Commitment

from intelligence.clock import now_iso
from intelligence.models import TurnInput
from intelligence.pipeline import BrainPipeline
from shared.persist_handoff import PersistPort

_default_pipeline: BrainPipeline | None = None


def get_pipeline(persist: Optional[PersistPort] = None) -> BrainPipeline:
    """Return the process pipeline, creating it on first use.

    Pass `persist` to install a sink. A later non-null `persist` replaces the
    sink on the existing pipeline. POST /v1/turn still returns only TurnResult.
    """
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = BrainPipeline(fixture_mode=True, persist=persist)
    elif persist is not None:
        _default_pipeline.persist = persist
    return _default_pipeline


def handle_turn_payload(
    payload: dict[str, Any],
    *,
    pipeline: Optional[BrainPipeline] = None,
) -> dict[str, Any]:
    """Run one text turn. Raises ValueError on a malformed body."""
    missing = [key for key in ("session_id", "turn_id", "speaker_role", "text") if not payload.get(key)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if not isinstance(payload["text"], str):
        raise ValueError("text must be a string")

    commitments: list[Commitment] = []
    for item in payload.get("active_commitments") or []:
        if isinstance(item, Commitment):
            commitments.append(item)
        elif isinstance(item, dict):
            commitments.append(Commitment.from_dict(item))
        else:
            raise ValueError("active_commitments entries must be objects")

    raw_conversation = payload.get("conversation_id")
    conversation_id = str(raw_conversation).strip() if raw_conversation else None
    raw_reason = payload.get("intervention_reason")
    intervention_reason = str(raw_reason).strip() if raw_reason else None

    incoming = TurnInput(
        session_id=str(payload["session_id"]),
        turn_id=str(payload["turn_id"]),
        speaker_role=str(payload["speaker_role"]),
        text=payload["text"],
        created_at=str(payload.get("created_at") or now_iso()),
        intervened=bool(payload.get("intervened", False)),
        active_commitments=commitments,
        conversation_id=conversation_id or None,
        intervention_reason=intervention_reason or None,
    )
    brain = pipeline or get_pipeline()
    return brain.handle_turn(incoming).to_dict()


def create_app(
    pipeline: Optional[BrainPipeline] = None,
    persist: Optional[PersistPort] = None,
):
    """Build a FastAPI app with POST /v1/turn and GET /health.

    Pass `persist` to install a Vault sink. When `pipeline` is omitted, a
    fixture-mode pipeline is created with that sink. When both are passed,
    `persist` replaces the pipeline sink. The response body stays TurnResult.
    """
    try:
        from fastapi import FastAPI, HTTPException
        from pydantic import BaseModel, Field
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "FastAPI is not installed. Install the brain extra: pip install fastapi"
        ) from exc

    if pipeline is None:
        brain = BrainPipeline(fixture_mode=True, persist=persist)
    else:
        brain = pipeline
        if persist is not None:
            brain.persist = persist

    class TurnIn(BaseModel):
        session_id: str
        turn_id: str
        speaker_role: str
        text: str
        created_at: str | None = None
        intervened: bool = False
        active_commitments: list[dict[str, Any]] = Field(default_factory=list)
        conversation_id: str | None = None
        intervention_reason: str | None = None

    app = FastAPI(title="VeraLock Brain", version="0.1.0")
    app.state.pipeline = brain

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "brain"}

    @app.post("/v1/turn")
    def turn(body: TurnIn) -> dict[str, Any]:
        data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
        try:
            return handle_turn_payload(data, pipeline=app.state.pipeline)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
