"""Turn audio → text.

``TurnAudioAdapter`` is the Brain kickoff protocol: audio bytes or a filesystem
path in, transcript text out. ``StubASR`` is the offline default. The OpenAI
Whisper adapter is constructed only when Voice ASR env vars select it and
``OPENAI_API_KEY`` is set. Importing this module never contacts the network
and never requires a key.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_TRANSCRIPT = "I will send the proposal by Friday."

# Explicit backend selection. A key alone does not turn on network ASR.
ASR_BACKEND_ENV = "VERALOCK_VOICE_ASR"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_TRANSCRIBE_MODEL"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"
_OPENAI_BACKENDS = frozenset({"openai", "whisper"})


@runtime_checkable
class TurnAudioAdapter(Protocol):
    """Audio bytes or a filesystem path to transcript text."""

    def transcribe(self, audio: bytes | str | Path) -> str:
        """Return transcript text. Implementations must not send audio to Brain."""


class StubASR:
    """Deterministic ASR for tests and CI.

    Ignores PCM bytes and does not read or open ``audio`` when it is a path.
    No network and no API key.
    """

    def __init__(self, text: str = DEFAULT_TRANSCRIPT) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("stub transcript must be a non-empty string")
        self.text = text.strip()

    def transcribe(self, audio: bytes | str | Path) -> str:
        _check_audio_input(audio)
        return self.text


class OpenAIWhisperAdapter:
    """OpenAI audio transcriptions (Whisper) over HTTPS.

    Construct this only when ``OPENAI_API_KEY`` is present. ``transcribe`` is
    the call that touches the network. CI should use ``StubASR`` instead.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "whisper-1",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError(f"{OPENAI_API_KEY_ENV} is required to use OpenAI Whisper")
        if not model.strip():
            raise ValueError("transcription model is required")
        self._api_key = key
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def transcribe(self, audio: bytes | str | Path) -> str:
        payload, filename = _load_audio(audio)
        if not payload:
            raise ValueError("audio bytes are empty")
        url = f"{self.base_url}/audio/transcriptions"
        body, content_type = _multipart(
            fields={"model": self.model},
            filename=filename,
            content=payload,
            content_type=_content_type(filename),
        )
        request = Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": content_type,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"OpenAI transcription failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"OpenAI transcription failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI transcription returned non-JSON") from exc
        text = parsed.get("text") if isinstance(parsed, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("OpenAI transcription returned empty text")
        return text.strip()


def turn_audio_adapter_from_env(text: str = DEFAULT_TRANSCRIPT) -> TurnAudioAdapter:
    """Return StubASR unless Whisper/OpenAI is explicitly selected and a key is set.

    Activation requires both ``VERALOCK_VOICE_ASR`` in ``{openai, whisper}`` and
    a non-empty ``OPENAI_API_KEY``. Otherwise this returns ``StubASR`` and does
    not read further credentials. Safe to call in CI with no env configured.
    """
    backend = os.environ.get(ASR_BACKEND_ENV, "").strip().lower()
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    if backend in _OPENAI_BACKENDS and api_key:
        model = os.environ.get(OPENAI_MODEL_ENV, "whisper-1")
        base_url = os.environ.get(OPENAI_BASE_URL_ENV, "https://api.openai.com/v1")
        return OpenAIWhisperAdapter(api_key, model=model, base_url=base_url)
    return StubASR(text)


def _check_audio_input(audio: bytes | str | Path) -> None:
    if isinstance(audio, (bytes, bytearray)):
        return
    if isinstance(audio, Path):
        return
    if isinstance(audio, str):
        if not audio.strip():
            raise ValueError("audio path is empty")
        return
    raise TypeError(f"audio must be bytes or a path, got {type(audio)!r}")


def _load_audio(audio: bytes | str | Path) -> tuple[bytes, str]:
    """Read bytes for a live transcription call. StubASR does not use this."""
    _check_audio_input(audio)
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio), "audio.wav"
    path = audio if isinstance(audio, Path) else Path(audio)
    if not path.is_file():
        raise FileNotFoundError(f"audio path not found: {path}")
    return path.read_bytes(), path.name or "audio.wav"


def _content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
    }.get(suffix, "application/octet-stream")


def _multipart(
    *,
    fields: dict[str, str],
    filename: str,
    content: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    boundary = f"----VeraLockVoice{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    safe_name = Path(filename).name.replace('"', "") or "audio.wav"
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(content)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    header = f"multipart/form-data; boundary={boundary}"
    return b"".join(chunks), header
