#!/usr/bin/env python3
"""Offline smoke test for the VeraLock Voice package.

Run from repo root:
  python scripts/smoke_voice.py

Uses ``StubASR`` only. Exits 0 with no microphone, API key, or network.
Prints a ``POST /v1/turn`` body. Brain is not required; when
``handle_turn_payload`` imports, the script posts that body as well.
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
    StubASR,
    build_turn,
    new_session_id,
    now_iso,
    validate_turn_payload,
)


def main() -> int:
    session_id = new_session_id()
    asr = StubASR()
    # Bytes stand in for a mic buffer. StubASR does not decode them.
    transcript = asr.transcribe(b"\x00\x00" * 160)
    turn = build_turn(
        transcript,
        session_id=session_id,
        speaker_role="user",
        created_at=now_iso(),
    )
    validate_turn_payload(turn)

    # Text-first: same builder, no ASR and no created_at.
    text_turn = build_turn(
        "I will send the proposal by Friday.",
        session_id=session_id,
        speaker_role="user",
    )
    validate_turn_payload(text_turn)

    assert turn["session_id"] == text_turn["session_id"] == session_id
    assert turn["turn_id"] != text_turn["turn_id"]
    assert turn["speaker_role"] == "user"
    assert turn["text"] == asr.text
    assert "created_at" in turn and "created_at" not in text_turn
    for payload in (turn, text_turn):
        for key in ("session_id", "turn_id", "speaker_role", "text"):
            assert isinstance(payload[key], str) and payload[key]
        assert "audio" not in payload and "pcm" not in payload

    print("StubASR turn payload:")
    print(json.dumps(turn, indent=2))
    print("\ntext-first turn payload:")
    print(json.dumps(text_turn, indent=2))

    try:
        from intelligence.api import handle_turn_payload
        from intelligence.pipeline import BrainPipeline
    except ImportError as exc:
        print(f"\nBrain import skipped: {exc}")
    else:
        result = handle_turn_payload(turn, pipeline=BrainPipeline())
        action = result.get("speech_action")
        if action not in ("SILENT", "CLARIFY"):
            raise RuntimeError(f"unexpected speech_action: {action!r}")
        print(f"\nBrain handle_turn_payload speech_action={action}")

    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
