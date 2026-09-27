"""Tests for AssemblyAI Voice Agent integration layer (voice/agent_server.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from intelligence.pipeline import BrainPipeline
from voice.agent_server import create_voice_app


def test_voice_app_health_and_index():
    app = create_voice_app(pipeline=BrainPipeline(fixture_mode=True))
    client = TestClient(app)

    # Health check
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "component": "voice_agent"}

    # Index html
    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "Frost & Flow HVAC" in res_index.text


def test_voice_app_turn_endpoint():
    app = create_voice_app(pipeline=BrainPipeline(fixture_mode=True))
    client = TestClient(app)

    payload = {
        "session_id": "sess-voice-test",
        "turn_id": "turn-voice-1",
        "speaker_role": "user",
        "text": "I will send the diagnostic report by 10 AM tomorrow.",
    }
    res = client.post("/v1/turn", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["speech_action"] == "SILENT"
    assert body["commitment"]["status"] == "CONFIRMED"


def test_voice_agent_websocket_flow():
    app = create_voice_app(pipeline=BrainPipeline(fixture_mode=True))
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        # Receive session.ready event
        msg = websocket.receive_json()
        assert msg["type"] == "session.ready"
        assert "session_id" in msg

        # Send test transcript through voice agent
        websocket.send_json(
            {
                "type": "test_transcript",
                "text": "I will inspect the compressor at 10 AM tomorrow.",
            }
        )

        # Receive final transcript event
        final_tx = websocket.receive_json()
        assert final_tx["type"] == "final_transcript"
        assert final_tx["text"] == "I will inspect the compressor at 10 AM tomorrow."

        # Receive turn result event
        result_msg = websocket.receive_json()
        assert result_msg["type"] == "turn_result"
        result = result_msg["result"]
        assert result["speech_action"] == "SILENT"
        assert result["commitment"]["status"] == "CONFIRMED"
