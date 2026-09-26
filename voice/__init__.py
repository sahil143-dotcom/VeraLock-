"""VeraLock Voice: capture stubs, ASR adapters, and text turn emission.

Voice never sends audio to Brain. It builds the JSON body for ``POST /v1/turn``.
Brain does not import this package and stays runnable without it.
"""

from voice.asr import AsrAdapter, StubAsrAdapter, Transcript
from voice.capture import AudioChunk, Capture, FakeCapture, MicCapture, StreamCapture
from voice.pipeline import VoicePipeline
from voice.turn_emit import (
    build_turn_payload,
    new_session_id,
    new_turn_id,
    validate_turn_payload,
)

__all__ = [
    "AsrAdapter",
    "AudioChunk",
    "Capture",
    "FakeCapture",
    "MicCapture",
    "StreamCapture",
    "StubAsrAdapter",
    "Transcript",
    "VoicePipeline",
    "build_turn_payload",
    "new_session_id",
    "new_turn_id",
    "validate_turn_payload",
]
