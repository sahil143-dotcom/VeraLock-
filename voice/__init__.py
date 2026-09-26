"""VeraLock Voice: audio to a text ``POST /v1/turn`` body.

Brain does not import this package. Voice does not import Brain. Audio never
enters the turn payload.
"""

from voice.capture import AudioChunk, Capture, FakeCapture, MicCapture, StreamCapture
from voice.pipeline import VoicePipeline
from voice.turn_audio import (
    OpenAIWhisperAdapter,
    StubASR,
    TurnAudioAdapter,
    turn_audio_adapter_from_env,
)
from voice.turn_builder import build_turn, new_session_id, new_turn_id, now_iso, validate_turn_payload

__all__ = [
    "AudioChunk",
    "Capture",
    "FakeCapture",
    "MicCapture",
    "OpenAIWhisperAdapter",
    "StreamCapture",
    "StubASR",
    "TurnAudioAdapter",
    "VoicePipeline",
    "build_turn",
    "new_session_id",
    "new_turn_id",
    "now_iso",
    "turn_audio_adapter_from_env",
    "validate_turn_payload",
]
