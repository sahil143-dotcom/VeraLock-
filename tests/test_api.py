"""HTTP entrypoint. Uses the in-process pipeline; no API keys."""

from __future__ import annotations

import pytest

from intelligence.api import create_app, handle_turn_payload
from intelligence.pipeline import BrainPipeline


def test_handle_turn_payload_ack():
    body = handle_turn_payload(
        {
            "session_id": "sess-api",
            "turn_id": "turn-1",
            "speaker_role": "user",
            "text": "Got it.",
        },
        pipeline=BrainPipeline(),
    )
    assert body["speech_action"] == "SILENT"
    assert body["commitment"]["status"] == "NO_COMMITMENT"
    assert body["commitment"]["is_acknowledgement"] is True


def test_handle_turn_payload_requires_text():
    with pytest.raises(ValueError):
        handle_turn_payload(
            {"session_id": "s", "turn_id": "t", "speaker_role": "user", "text": ""},
            pipeline=BrainPipeline(),
        )


def test_fastapi_turn_and_health():
    fastapi = pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app = create_app(pipeline=BrainPipeline())
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "component": "brain"}

    clear = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-http",
            "turn_id": "turn-1",
            "speaker_role": "user",
            "text": "I will send the proposal by Friday.",
        },
    )
    assert clear.status_code == 200
    payload = clear.json()
    assert payload["speech_action"] == "SILENT"
    assert payload["commitment"]["status"] == "CONFIRMED"

    missing = client.post(
        "/v1/turn",
        json={
            "session_id": "sess-http",
            "turn_id": "turn-2",
            "speaker_role": "user",
            "text": "",
        },
    )
    assert missing.status_code == 400
    assert fastapi is not None
