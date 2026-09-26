"""ASR adapter interface and an offline stub.

The stub returns canned transcript text. It does not load a model, read API
keys, or use the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable

from voice.capture import AudioChunk

DEFAULT_TRANSCRIPT = "I will send the proposal by Friday."


@dataclass(frozen=True)
class Transcript:
    """Text result of one ASR call."""

    text: str
    is_final: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("transcript text must be a non-empty string")


@runtime_checkable
class AsrAdapter(Protocol):
    """Transcribe one audio chunk into text. Implementations stay offline by default."""

    def transcribe(self, audio: AudioChunk | bytes) -> Transcript:
        """Return a transcript. Must not send audio to Brain or to the network."""


class StubAsrAdapter:
    """Deterministic ASR. Ignores PCM and returns a canned line.

    ``by_label`` overrides the canned line when ``AudioChunk.label`` matches.
    Unknown labels and raw ``bytes`` use ``transcript``.
    """

    def __init__(
        self,
        transcript: str = DEFAULT_TRANSCRIPT,
        *,
        by_label: Mapping[str, str] | None = None,
    ) -> None:
        if not isinstance(transcript, str) or not transcript.strip():
            raise ValueError("stub transcript must be a non-empty string")
        self.transcript = transcript.strip()
        self.by_label = {str(key): value.strip() for key, value in dict(by_label or {}).items()}
        for label, text in self.by_label.items():
            if not label:
                raise ValueError("ASR label must be non-empty")
            if not text:
                raise ValueError(f"ASR text for label {label!r} must be non-empty")

    def transcribe(self, audio: AudioChunk | bytes) -> Transcript:
        text = self.transcript
        if isinstance(audio, AudioChunk) and audio.label:
            text = self.by_label.get(audio.label, self.transcript)
        elif not isinstance(audio, (AudioChunk, bytes, bytearray)):
            raise TypeError(f"audio must be AudioChunk or bytes, got {type(audio)!r}")
        return Transcript(text=text, is_final=True)
