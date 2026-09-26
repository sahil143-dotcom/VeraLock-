#!/usr/bin/env python3
"""Offline smoke test for the VeraLock Voice package.

Run from repo root:
  python scripts/smoke_voice.py

Exits 0 with no microphone, API key, or network. Prints a sample ``POST /v1/turn``
body. When Brain imports cleanly, also posts that body through
``handle_turn_payload``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow imports when run as scripts/smoke_voice.py from repo root.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from voice import (  # noqa: E402
    FakeCapture,
    MicCapture,
    StubAsrAdapter,
    VoicePipeline,
    new_session_id,
    validate_turn_payload,
)


def main() -> int:
    session_id = new_session_id()
    text_pipeline = VoicePipeline(session_id=session_id, speaker_role="user")
    text_turn = text_pipeline.emit_text("I will send the proposal by Friday.")
    validate_turn_payload(text_turn)

    audio_pipeline = VoicePipeline(
        session_id=session_id,
        speaker_role="user",
        asr=StubAsrAdapter(),
        capture=FakeCapture(),
    )
    audio_turn = audio_pipeline.emit_audio()
    validate_turn_payload(audio_turn)

    # Mic stub must not yield samples and must not be required for the demo.
    mic = MicCapture(device_name="default")
    with mic:
        assert mic.read() is None

    assert text_turn["session_id"] == audio_turn["session_id"] == session_id
    assert text_turn["turn_id"] != audio_turn["turn_id"]
    assert text_turn["speaker_role"] == "user"
    assert audio_turn["text"] == "I will send the proposal by Friday."
    for payload in (text_turn, audio_turn):
        for key in ("session_id", "turn_id", "speaker_role", "text"):
            assert isinstance(payload[key], str) and payload[key]
        assert "audio" not in payload and "pcm" not in payload

    print("text-first turn payload:")
    print(json.dumps(text_turn, indent=2))
    print("\ncapture → stub ASR turn payload:")
    print(json.dumps(audio_turn, indent=2))

    try:
        from intelligence.api import handle_turn_payload
        from intelligence.pipeline import BrainPipeline
    except ImportError as exc:
        print(f"\nBrain import skipped: {exc}")
    else:
        result = handle_turn_payload(text_turn, pipeline=BrainPipeline())
        print("\nBrain handle_turn_payload:")
        print(json.dumps(result, indent=2))
        if "speech_action" not in result:
            raise RuntimeError("Brain result missing speech_action")

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
