"""Text-first turn emission, with an optional capture → ASR path.

``emit_text`` needs no microphone and no ASR. ``emit_audio`` reads a capture
stub, transcribes with an ``AsrAdapter``, and emits the same turn body.
"""

from __future__ import annotations

from typing import Any

from voice.asr import AsrAdapter, Transcript
from voice.capture import Capture
from voice.turn_emit import build_turn_payload, new_session_id


class VoicePipeline:
    """Hold a reusable ``session_id`` and emit Brain turn payloads.

    Audio chunks are consumed here and dropped. Only the transcript text is
    placed on the payload.
    """

    def __init__(
        self,
        *,
        session_id: str | None = None,
        speaker_role: str = "user",
        asr: AsrAdapter | None = None,
        capture: Capture | None = None,
        intervened: bool = False,
    ) -> None:
        self.session_id = session_id or new_session_id()
        self.speaker_role = speaker_role
        self.asr = asr
        self.capture = capture
        self.intervened = intervened

    def emit_text(
        self,
        text: str,
        *,
        turn_id: str | None = None,
        speaker_role: str | None = None,
        created_at: str | None = None,
        intervened: bool | None = None,
        active_commitments: list[dict[str, Any]] | None = None,
        conversation_id: str | None = None,
        intervention_reason: str | None = None,
    ) -> dict[str, Any]:
        """Build a turn from plain text. This is the default demo path."""
        return build_turn_payload(
            text,
            session_id=self.session_id,
            turn_id=turn_id,
            speaker_role=self.speaker_role if speaker_role is None else speaker_role,
            created_at=created_at,
            intervened=self.intervened if intervened is None else intervened,
            active_commitments=active_commitments,
            conversation_id=conversation_id,
            intervention_reason=intervention_reason,
        )

    def emit_audio(
        self,
        capture: Capture | None = None,
        *,
        turn_id: str | None = None,
        speaker_role: str | None = None,
        created_at: str | None = None,
        intervened: bool | None = None,
        active_commitments: list[dict[str, Any]] | None = None,
        conversation_id: str | None = None,
        intervention_reason: str | None = None,
    ) -> dict[str, Any]:
        """Capture (or replay) audio, transcribe it, and emit a text turn."""
        source = capture if capture is not None else self.capture
        if source is None:
            raise RuntimeError("no capture configured")
        if self.asr is None:
            raise RuntimeError("no ASR adapter configured")
        text = transcribe_capture(source, self.asr)
        return self.emit_text(
            text,
            turn_id=turn_id,
            speaker_role=speaker_role,
            created_at=created_at,
            intervened=intervened,
            active_commitments=active_commitments,
            conversation_id=conversation_id,
            intervention_reason=intervention_reason,
        )


def transcribe_capture(capture: Capture, asr: AsrAdapter) -> str:
    """Read every chunk from ``capture`` and join stub transcripts.

    Consecutive duplicate lines are collapsed so a canned adapter does not
    repeat itself once per frame.
    """
    parts: list[str] = []
    with capture:
        while True:
            chunk = capture.read()
            if chunk is None:
                break
            parts.append(_transcript_text(asr.transcribe(chunk)))
    if not parts:
        raise RuntimeError("capture produced no audio")
    deduped: list[str] = []
    for part in parts:
        if not deduped or deduped[-1] != part:
            deduped.append(part)
    return " ".join(deduped)


def _transcript_text(result: Transcript | str) -> str:
    if isinstance(result, Transcript):
        text = result.text
    elif isinstance(result, str):
        text = result
    else:
        raise TypeError(f"ASR result must be Transcript or str, got {type(result)!r}")
    stripped = text.strip()
    if not stripped:
        raise ValueError("ASR returned empty text")
    return stripped
