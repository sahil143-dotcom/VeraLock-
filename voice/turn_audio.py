"""Turn audio → text.

``TurnAudioAdapter`` is the Brain kickoff protocol: audio bytes or a filesystem
path in, transcript text out. ``StubASR`` is the offline default.

Two live adapters are supported:

* ``OpenAIWhisperAdapter``: selected when ``VERALOCK_VOICE_ASR`` is ``openai``
  or ``whisper`` and ``OPENAI_API_KEY`` is set.

* ``AssemblyAIAdapter``: selected when ``VERALOCK_VOICE_ASR=assemblyai`` and
  ``ASSEMBLYAI_API_KEY`` is set. Uploads audio then polls for the completed
  transcript using the AssemblyAI v2 REST API (stdlib only, no SDK).

Importing this module never contacts the network and never requires a key.
"""

from __future__ import annotations

import json
import os
import time
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
ASSEMBLYAI_API_KEY_ENV = "ASSEMBLYAI_API_KEY"
ASSEMBLYAI_API_BASE = "https://api.assemblyai.com"
_OPENAI_BACKENDS = frozenset({"openai", "whisper"})
_ASSEMBLYAI_BACKENDS = frozenset({"assemblyai", "aai"})


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


class AssemblyAIAdapter:
    """AssemblyAI audio transcription via the v2 REST API (stdlib only, no SDK).

    Two-step flow:
    1. ``POST /v2/upload`` – upload raw audio bytes; returns ``upload_url``.
    2. ``POST /v2/transcript`` – start a transcription job; returns ``id``.
    3. Poll ``GET /v2/transcript/{id}`` until ``status`` is ``completed``
       or ``error``.

    All network I/O happens inside ``transcribe``; constructing this class is
    safe in CI as long as ``transcribe`` is not called.
    """

    def __init__(
        self,
        api_key: str,
        *,
        poll_interval: float = 1.5,
        timeout: float = 120.0,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError(f"{ASSEMBLYAI_API_KEY_ENV} is required for AssemblyAI")
        self._api_key = key
        self.poll_interval = max(0.5, poll_interval)
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

    def transcribe(self, audio: bytes | str | Path) -> str:
        payload, _ = _load_audio(audio)
        if not payload:
            raise ValueError("audio bytes are empty")
        upload_url = self._upload(payload)
        transcript_id = self._submit(upload_url)
        return self._poll(transcript_id)

    def _upload(self, data: bytes) -> str:
        url = f"{ASSEMBLYAI_API_BASE}/v2/upload"
        req = Request(
            url,
            data=data,
            headers={"Authorization": self._api_key, "Content-Type": "application/octet-stream"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"AssemblyAI upload failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"AssemblyAI upload failed: {exc.reason}") from exc
        body = json.loads(raw.decode("utf-8"))
        upload_url = body.get("upload_url") if isinstance(body, dict) else None
        if not isinstance(upload_url, str) or not upload_url.strip():
            raise RuntimeError("AssemblyAI upload returned no upload_url")
        return upload_url

    def _submit(self, upload_url: str) -> str:
        url = f"{ASSEMBLYAI_API_BASE}/v2/transcript"
        body = json.dumps({"audio_url": upload_url}).encode("utf-8")
        req = Request(url, data=body, headers=self._headers(), method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"AssemblyAI submit failed: HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"AssemblyAI submit failed: {exc.reason}") from exc
        data = json.loads(raw.decode("utf-8"))
        transcript_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(transcript_id, str) or not transcript_id.strip():
            raise RuntimeError("AssemblyAI submit returned no transcript id")
        return transcript_id

    def _poll(self, transcript_id: str) -> str:
        url = f"{ASSEMBLYAI_API_BASE}/v2/transcript/{transcript_id}"
        headers = {"Authorization": self._api_key}
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            req = Request(url, headers=headers, method="GET")
            try:
                with urlopen(req, timeout=30) as resp:
                    raw = resp.read()
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"AssemblyAI poll failed: HTTP {exc.code} {detail}") from exc
            except URLError as exc:
                raise RuntimeError(f"AssemblyAI poll failed: {exc.reason}") from exc
            data = json.loads(raw.decode("utf-8"))
            status = data.get("status") if isinstance(data, dict) else None
            if status == "completed":
                text = data.get("text")
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("AssemblyAI completed but returned empty text")
                return text.strip()
            if status == "error":
                error = data.get("error", "unknown error")
                raise RuntimeError(f"AssemblyAI transcription error: {error}")
            time.sleep(self.poll_interval)
        raise RuntimeError(
            f"AssemblyAI transcription timed out after {self.timeout}s "
            f"(transcript_id={transcript_id})"
        )


def turn_audio_adapter_from_env(text: str = DEFAULT_TRANSCRIPT) -> TurnAudioAdapter:
    """Return the appropriate ASR adapter based on env vars.

    Priority:
    1. ``VERALOCK_VOICE_ASR=assemblyai`` + ``ASSEMBLYAI_API_KEY`` set
       → ``AssemblyAIAdapter``.
    2. ``VERALOCK_VOICE_ASR`` in ``{openai, whisper}`` + ``OPENAI_API_KEY`` set
       → ``OpenAIWhisperAdapter``.
    3. Fallback → ``StubASR`` (offline, deterministic).
    """
    backend = os.environ.get(ASR_BACKEND_ENV, "").strip().lower()
    if backend in _ASSEMBLYAI_BACKENDS:
        aai_key = os.environ.get(ASSEMBLYAI_API_KEY_ENV, "").strip()
        if aai_key:
            return AssemblyAIAdapter(aai_key)
    if backend in _OPENAI_BACKENDS:
        api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
        if api_key:
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
