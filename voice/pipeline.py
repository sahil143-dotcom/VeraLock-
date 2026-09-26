"""Text-first turn emission, plus capture bytes fed through a ``TurnAudioAdapter``.

``emit_text`` does not call ASR. ``emit_audio`` transcribes bytes or a path,
or concatenates PCM from a capture stub, then calls ``build_turn``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from voice.capture import Capture
from voice.turn_audio import StubASR, TurnAudioAdapter
from voice.turn_builder import build_turn, new_session_id


class VoicePipeline:
    """Reusable ``session_id`` and Brain turn payloads.

    The default adapter is ``StubASR``, so a pipeline can be built with no API
    key. Audio bytes are dropped after transcription.
    """

    def __init__(
        self,
        *,
        session_id: str | None = None,
        speaker_role: str = "user",
        asr: TurnAudioAdapter | None = None,
        capture: Capture | None = None,
    ) -> None:
        self.session_id = session_id or new_session_id()
        self.speaker_role = speaker_role
        self.asr: TurnAudioAdapter = asr if asr is not None else StubASR()
        self.capture = capture

    def emit_text(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        speaker_role: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Build a turn from plain text. No microphone and no ASR."""
        return build_turn(
            text,
            session_id=self.session_id,
            turn_id=turn_id,
            speaker_role=self.speaker_role if speaker_role is None else speaker_role,
            created_at=created_at,
        )

    def emit_audio(
        self,
        audio: bytes | str | Path | None = None,
        *,
        turn_id: str | None = None,
        speaker_role: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Transcribe ``audio`` (or the configured capture) and emit a text turn."""
        source: bytes | str | Path
        if audio is None:
            if self.capture is None:
                raise RuntimeError("no audio and no capture configured")
            source = read_capture_pcm(self.capture)
        else:
            source = audio
        text = self.asr.transcribe(source)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("ASR returned empty text")
        return self.emit_text(
            text.strip(),
            turn_id=turn_id,
            speaker_role=speaker_role,
            created_at=created_at,
        )


def read_capture_pcm(capture: Capture) -> bytes:
    """Concatenate PCM from a capture stub. Does not open a microphone."""
    frames: list[bytes] = []
    with capture:
        while True:
            chunk = capture.read()
            if chunk is None:
                break
            if chunk.pcm:
                frames.append(chunk.pcm)
    if not frames:
        raise RuntimeError("capture produced no audio")
    return b"".join(frames)
