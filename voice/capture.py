"""Microphone and stream capture stubs.

Nothing here opens a sound device, socket, or file on the host. CI uses
``FakeCapture``. ``MicCapture`` is a no-op interface placeholder.
``StreamCapture`` replays an in-memory sequence of chunks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True)
class AudioChunk:
    """One PCM frame. This object stays inside Voice and is never posted to Brain."""

    pcm: bytes
    sample_rate: int = 16000
    channels: int = 1
    label: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.pcm, (bytes, bytearray)):
            raise TypeError("pcm must be bytes")
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.channels <= 0:
            raise ValueError("channels must be positive")
        object.__setattr__(self, "pcm", bytes(self.pcm))


class Capture(ABC):
    """Start / read / stop lifecycle for a chunk source."""

    @abstractmethod
    def start(self) -> None:
        """Begin a capture session. Must not touch live audio hardware."""

    @abstractmethod
    def read(self) -> AudioChunk | None:
        """Return the next chunk, or None when the source is exhausted."""

    @abstractmethod
    def stop(self) -> None:
        """End the capture session and release any stub state."""

    def __enter__(self) -> "Capture":
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()

    def chunks(self) -> Iterator[AudioChunk]:
        """Yield chunks until the source is exhausted, then stop."""
        self.start()
        try:
            while True:
                chunk = self.read()
                if chunk is None:
                    break
                yield chunk
        finally:
            self.stop()


class MicCapture(Capture):
    """No-op microphone stub.

    ``device_name`` is stored for a future live adapter. ``start``, ``read``,
    and ``stop`` do not open a microphone. ``read`` returns None.
    """

    def __init__(self, device_name: str | None = None) -> None:
        self.device_name = device_name
        self._started = False

    def start(self) -> None:
        self._started = True

    def read(self) -> AudioChunk | None:
        if not self._started:
            raise RuntimeError("MicCapture.read() called before start()")
        return None

    def stop(self) -> None:
        self._started = False


class StreamCapture(Capture):
    """Replay an in-memory byte stream or chunk sequence.

    Accepts raw PCM ``bytes`` (one chunk) or an iterable of ``AudioChunk`` /
    ``bytes``. No socket or file is opened.
    """

    def __init__(
        self,
        source: Iterable[AudioChunk | bytes] | bytes | None = None,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self._pending = _materialize(source, sample_rate=sample_rate, channels=channels)
        self._index = 0
        self._started = False

    def start(self) -> None:
        self._started = True
        self._index = 0

    def read(self) -> AudioChunk | None:
        if not self._started:
            raise RuntimeError("StreamCapture.read() called before start()")
        if self._index >= len(self._pending):
            return None
        chunk = self._pending[self._index]
        self._index += 1
        return chunk

    def stop(self) -> None:
        self._started = False


class FakeCapture(StreamCapture):
    """Deterministic capture for tests and the smoke script.

    Yields one silent frame labeled ``canned`` unless the caller supplies chunks.
    """

    def __init__(
        self,
        chunks: Iterable[AudioChunk | bytes] | bytes | None = None,
        *,
        label: str = "canned",
        sample_rate: int = 16000,
    ) -> None:
        if chunks is None:
            chunks = [
                AudioChunk(
                    pcm=b"\x00\x00" * 160,
                    sample_rate=sample_rate,
                    label=label,
                )
            ]
        super().__init__(chunks, sample_rate=sample_rate)


def _materialize(
    source: Iterable[AudioChunk | bytes] | bytes | None,
    *,
    sample_rate: int,
    channels: int,
) -> list[AudioChunk]:
    if source is None:
        return []
    if isinstance(source, (bytes, bytearray)):
        return [AudioChunk(pcm=bytes(source), sample_rate=sample_rate, channels=channels)]
    chunks: list[AudioChunk] = []
    for item in source:
        if isinstance(item, AudioChunk):
            chunks.append(item)
        elif isinstance(item, (bytes, bytearray)):
            chunks.append(
                AudioChunk(pcm=bytes(item), sample_rate=sample_rate, channels=channels)
            )
        else:
            raise TypeError(f"unsupported capture item: {type(item)!r}")
    return chunks
