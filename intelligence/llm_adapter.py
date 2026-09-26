"""OpenAI-compatible chat completions client for the Brain reasoner.

Stdlib only. No SDK. The client is selected by `Reasoner` when fixture mode
is off and an API key is present. It never reads a key at import time.

One `complete(prompt)` call posts one non-streaming chat completion and
returns the assistant message text. JSON mode is the default
(`response_format: json_object`). `json_schema` sends the same A→G schema
`parse_ag` accepts.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, Optional

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_RESPONSE_FORMAT = "json_object"

# Proposal statuses the reasoner prompt asks for. Guardrails, not the model,
# apply SUPERSEDED / WITHDRAWN.
_PROPOSAL_STATUSES = [
    "NO_COMMITMENT",
    "DETECTED",
    "AWAITING_CLARIFICATION",
    "CONFIRMED",
    "UNRESOLVED_AMBIGUOUS",
]

_SURFACES = [
    "acknowledgement",
    "intention",
    "commitment",
    "ambiguous",
    "unrelated",
]

AG_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["A", "B", "C", "D", "E", "F", "G"],
    "properties": {
        "A": {
            "type": "object",
            "additionalProperties": False,
            "required": ["actor"],
            "properties": {"actor": {"type": "string"}},
        },
        "B": {
            "type": "object",
            "additionalProperties": False,
            "required": ["topic_id", "matched_commitment_id"],
            "properties": {
                "topic_id": {"type": "string"},
                "matched_commitment_id": {"type": ["string", "null"]},
            },
        },
        "C": {
            "type": "object",
            "additionalProperties": False,
            "required": ["surface"],
            "properties": {"surface": {"type": "string", "enum": list(_SURFACES)}},
        },
        "D": {
            "type": "object",
            "additionalProperties": False,
            "required": ["canonical_text", "raw_span"],
            "properties": {
                "canonical_text": {"type": "string"},
                "raw_span": {"type": ["string", "null"]},
            },
        },
        "E": {
            "type": "object",
            "additionalProperties": False,
            "required": ["confidence", "conditions", "source_turn_ids"],
            "properties": {
                "confidence": {"type": "number"},
                "conditions": {"type": ["object", "null"]},
                "source_turn_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "F": {
            "type": "object",
            "additionalProperties": False,
            "required": ["is_acknowledgement", "is_intention_only", "proposed_status"],
            "properties": {
                "is_acknowledgement": {"type": "boolean"},
                "is_intention_only": {"type": "boolean"},
                "proposed_status": {"type": "string", "enum": list(_PROPOSAL_STATUSES)},
            },
        },
        "G": {
            "type": "object",
            "additionalProperties": False,
            "required": ["suggested_speech", "clarification_question"],
            "properties": {
                "suggested_speech": {"type": "string", "enum": ["SILENT", "CLARIFY"]},
                "clarification_question": {"type": ["string", "null"]},
            },
        },
    },
}


class LLMAdapterError(RuntimeError):
    """Transport or response-shape failure. The message never includes the API key."""


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def _first_env(environ: Mapping[str, str], *names: str) -> Optional[str]:
    for name in names:
        raw = environ.get(name)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return None


def chat_completions_url(base_url: str) -> str:
    """API root (`…/v1`) or a full chat-completions URL, both accepted."""
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def normalize_response_format(raw: Optional[str]) -> str:
    """`json_object` (default), `json_schema`, or `off`."""
    if raw is None or not raw.strip():
        return DEFAULT_RESPONSE_FORMAT
    value = raw.strip().lower().replace("-", "_")
    if value in {"json_object", "json", "json_mode"}:
        return "json_object"
    if value in {"json_schema", "schema", "structured"}:
        return "json_schema"
    if value in {"off", "none", "text", "disabled"}:
        return "off"
    return DEFAULT_RESPONSE_FORMAT


def _timeout_seconds(raw: Optional[str]) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw.strip())
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS
    if value <= 0:
        return DEFAULT_TIMEOUT_SECONDS
    return value


def response_format_body(mode: str) -> Optional[dict[str, Any]]:
    if mode == "off":
        return None
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "veralock_reasoner_ag",
                "strict": False,
                "schema": AG_JSON_SCHEMA,
            },
        }
    return {"type": "json_object"}


def system_prompt() -> str:
    schema = json.dumps(AG_JSON_SCHEMA, separators=(",", ":"), sort_keys=True)
    return (
        "You are VeraLock Brain. Return one JSON object and nothing else. "
        "No markdown. The object must use keys A, B, C, D, E, F, and G only, "
        "matching this JSON Schema: "
        f"{schema}"
    )


def urllib_transport(
    url: str,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, bytes]:
    """POST via urllib. Raises LLMAdapterError. Does not include the key or body."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200))
            return status, response.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise LLMAdapterError(f"LLM HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise LLMAdapterError("LLM request timed out") from exc
        raise LLMAdapterError("LLM request failed") from exc
    except TimeoutError as exc:
        raise LLMAdapterError("LLM request timed out") from exc


def _message_text(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMAdapterError("LLM response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMAdapterError("LLM response missing choices")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMAdapterError("LLM response missing message")
    if message.get("refusal"):
        raise LLMAdapterError("LLM refused the completion")
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, dict):
        return json.dumps(content)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            return joined
    raise LLMAdapterError("LLM response missing message content")


class OpenAICompatibleLLM:
    """Single-shot chat completion. Implements the reasoner `LLMClient` protocol."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        response_format: str = DEFAULT_RESPONSE_FORMAT,
        transport: Optional[Transport] = None,
    ) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("api_key is empty")
        self._api_key = key
        self.base_url = base_url.strip() or DEFAULT_BASE_URL
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout = timeout if timeout > 0 else DEFAULT_TIMEOUT_SECONDS
        self.response_format = normalize_response_format(response_format)
        self.transport: Transport = transport or urllib_transport

    def __repr__(self) -> str:
        return (
            "OpenAICompatibleLLM("
            f"model={self.model!r}, base_url={self.base_url!r}, "
            f"response_format={self.response_format!r})"
        )

    def complete(self, prompt: str) -> str:
        """One chat completion. Returns the assistant text (the A→G JSON)."""
        url = chat_completions_url(self.base_url)
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt()},
                {"role": "user", "content": prompt},
            ],
        }
        fmt = response_format_body(self.response_format)
        if fmt is not None:
            payload["response_format"] = fmt
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            status, raw = self.transport(url, headers, body, self.timeout)
        except LLMAdapterError:
            raise
        except TimeoutError as exc:
            raise LLMAdapterError("LLM request timed out") from exc
        except OSError as exc:
            raise LLMAdapterError("LLM request failed") from exc
        if status >= 400:
            raise LLMAdapterError(f"LLM HTTP {status}")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LLMAdapterError("LLM response was not JSON") from exc
        if not isinstance(decoded, dict):
            raise LLMAdapterError("LLM response was not a JSON object")
        return _message_text(decoded)


def client_from_env(
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[OpenAICompatibleLLM]:
    """Build a client from the environment, or None when no key is set.

    `VERALOCK_LLM_API_KEY` wins over `OPENAI_API_KEY`. The same preference
    applies to base URL (`VERALOCK_LLM_BASE_URL` / `OPENAI_BASE_URL`) and
    model (`VERALOCK_LLM_MODEL` / `OPENAI_MODEL`).
    """
    env = os.environ if environ is None else environ
    api_key = _first_env(env, "VERALOCK_LLM_API_KEY", "OPENAI_API_KEY")
    if api_key is None:
        return None
    base_url = _first_env(env, "VERALOCK_LLM_BASE_URL", "OPENAI_BASE_URL") or DEFAULT_BASE_URL
    model = _first_env(env, "VERALOCK_LLM_MODEL", "OPENAI_MODEL") or DEFAULT_MODEL
    response_format = normalize_response_format(
        _first_env(env, "VERALOCK_LLM_RESPONSE_FORMAT")
    )
    timeout = _timeout_seconds(_first_env(env, "VERALOCK_LLM_TIMEOUT"))
    return OpenAICompatibleLLM(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout=timeout,
        response_format=response_format,
    )
