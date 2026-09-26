"""Optional Brain LLM adapter. No network and no real API key."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import EmailMessage

import pytest

from intelligence.context import ContextSnapshot
from intelligence.llm_adapter import (
    AG_JSON_SCHEMA,
    LLMAdapterError,
    OpenAICompatibleLLM,
    chat_completions_url,
    client_from_env,
    urllib_transport,
)
from intelligence.models import TurnInput
from intelligence.pipeline import BrainPipeline
from intelligence.reasoner import Reasoner, StubLLM, parse_ag
from shared.commitment_schema import CommitmentStatus


def _ag(*, surface: str = "commitment", speech: str = "SILENT", status: str = "CONFIRMED") -> str:
    return json.dumps(
        {
            "A": {"actor": "user"},
            "B": {"topic_id": "proposal-delivery", "matched_commitment_id": None},
            "C": {"surface": surface},
            "D": {
                "canonical_text": "Send the proposal by Friday.",
                "raw_span": "I will send the proposal by Friday.",
            },
            "E": {"confidence": 0.91, "conditions": None, "source_turn_ids": ["turn-1"]},
            "F": {
                "is_acknowledgement": False,
                "is_intention_only": False,
                "proposed_status": status,
            },
            "G": {"suggested_speech": speech, "clarification_question": None},
        }
    )


def _completion(content) -> bytes:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()


def _clear_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VERALOCK_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VERALOCK_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("VERALOCK_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.delenv("VERALOCK_LLM_RESPONSE_FORMAT", raising=False)
    monkeypatch.delenv("VERALOCK_LLM_TIMEOUT", raising=False)


def _snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        session_id="sess-1",
        turns=[],
        active_commitments=[],
        speaker_roles=["user"],
    )


def _turn(text: str) -> TurnInput:
    return TurnInput(
        session_id="sess-1",
        turn_id="turn-1",
        speaker_role="user",
        text=text,
        created_at="2026-09-26T00:00:00Z",
    )


def test_schema_matches_reasoner_ag_contract():
    assert list(AG_JSON_SCHEMA["required"]) == ["A", "B", "C", "D", "E", "F", "G"]
    surfaces = set(AG_JSON_SCHEMA["properties"]["C"]["properties"]["surface"]["enum"])
    assert surfaces == {
        "acknowledgement",
        "intention",
        "commitment",
        "ambiguous",
        "unrelated",
    }
    speeches = set(AG_JSON_SCHEMA["properties"]["G"]["properties"]["suggested_speech"]["enum"])
    assert speeches == {"SILENT", "CLARIFY"}
    statuses = set(AG_JSON_SCHEMA["properties"]["F"]["properties"]["proposed_status"]["enum"])
    assert statuses <= {item.value for item in CommitmentStatus}
    parsed = parse_ag(_ag())
    assert parsed.surface == "commitment"
    assert parsed.proposed_status == "CONFIRMED"


def test_chat_completions_url_accepts_root_or_full_path():
    assert chat_completions_url("https://api.openai.com/v1/") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert chat_completions_url("https://example.test/v1/chat/completions") == (
        "https://example.test/v1/chat/completions"
    )


def test_fixture_mode_ignores_api_key_and_injected_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other")

    def boom(*_args, **_kwargs):
        raise AssertionError("fixture mode must not touch the network")

    monkeypatch.setattr("intelligence.llm_adapter.urllib_transport", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    class Injected:
        def complete(self, prompt: str) -> str:
            raise AssertionError("fixture mode must ignore an injected client")

    reasoner = Reasoner(llm=Injected(), fixture_mode=True)
    assert isinstance(reasoner.llm, StubLLM)
    parsed = reasoner.reason(
        _snapshot(),
        text="I will send the proposal by Friday.",
        turn_id="turn-1",
        speaker_role="user",
        created_at="2026-09-26T00:00:00Z",
    )
    assert parsed.surface == "commitment"
    assert parsed.proposed_status == "CONFIRMED"

    pipeline = BrainPipeline()
    assert isinstance(pipeline.reasoner.llm, StubLLM)
    result = pipeline.handle_turn(_turn("hello"))
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.NO_COMMITMENT
    assert result.speech_action == "SILENT"


def test_missing_key_falls_back_to_stub(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "   ")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    def boom(*_args, **_kwargs):
        raise AssertionError("missing key must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)

    assert client_from_env() is None
    reasoner = Reasoner(fixture_mode=False)
    assert isinstance(reasoner.llm, StubLLM)

    pipeline = BrainPipeline(fixture_mode=False)
    result = pipeline.handle_turn(_turn("I will send the proposal by Friday."))
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.CONFIRMED
    assert result.speech_action == "SILENT"


def test_env_client_posts_json_mode_and_pipeline_uses_it(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-vera")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("VERALOCK_LLM_MODEL", "vera-model")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("VERALOCK_LLM_BASE_URL", "https://vera.example/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")

    captured: dict = {}

    def transport(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["timeout"] = timeout
        return 200, _completion(_ag())

    pipeline = BrainPipeline(fixture_mode=False)
    assert isinstance(pipeline.reasoner.llm, OpenAICompatibleLLM)
    pipeline.reasoner.llm.transport = transport
    # "hello" is unrelated for StubLLM. A confirmed trace proves the model path ran.
    result = pipeline.handle_turn(_turn("hello"))

    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.CONFIRMED
    assert result.speech_action == "SILENT"
    assert captured["url"] == "https://vera.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-vera"
    assert "sk-vera" not in captured["body"]["messages"][1]["content"]
    assert captured["body"]["model"] == "vera-model"
    assert captured["body"]["temperature"] == 0
    assert captured["body"]["stream"] is False
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["messages"][0]["role"] == "system"
    assert "JSON" in captured["body"]["messages"][0]["content"]
    assert "<brain_input>" in captured["body"]["messages"][1]["content"]
    assert "hello" in captured["body"]["messages"][1]["content"]
    assert "sk-vera" not in repr(pipeline.reasoner.llm)


def test_openai_env_names_are_the_fallback(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1/")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("VERALOCK_LLM_RESPONSE_FORMAT", "json_schema")
    monkeypatch.setenv("VERALOCK_LLM_TIMEOUT", "12")

    captured: dict = {}

    def transport(url, headers, body, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body.decode("utf-8"))
        captured["timeout"] = timeout
        return 200, _completion(_ag())

    client = client_from_env()
    assert client is not None
    client.transport = transport
    assert parse_ag(client.complete("prompt")).surface == "commitment"
    assert captured["url"] == "https://openai.example/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-openai"
    assert captured["timeout"] == 12
    assert captured["body"]["model"] == "openai-model"
    fmt = captured["body"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "veralock_reasoner_ag"
    assert fmt["json_schema"]["strict"] is False
    assert fmt["json_schema"]["schema"]["required"] == ["A", "B", "C", "D", "E", "F", "G"]


def test_response_format_off_omits_field(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("VERALOCK_LLM_RESPONSE_FORMAT", "off")
    captured: dict = {}

    def transport(url, headers, body, timeout):
        captured["body"] = json.loads(body.decode("utf-8"))
        return 200, _completion(_ag())

    client = client_from_env()
    assert client is not None
    client.transport = transport
    client.complete("prompt")
    assert "response_format" not in captured["body"]


def test_invalid_timeout_uses_default(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-vera")
    monkeypatch.setenv("VERALOCK_LLM_TIMEOUT", "nope")
    client = client_from_env()
    assert client is not None
    assert client.timeout == 30.0


def test_explicit_llm_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-secret")
    calls = {"n": 0}

    class OneShot:
        def complete(self, prompt: str) -> str:
            calls["n"] += 1
            return _ag()

    reasoner = Reasoner(llm=OneShot(), fixture_mode=False)
    assert isinstance(reasoner.llm, OneShot)
    parsed = reasoner.reason(
        _snapshot(),
        text="hello",
        turn_id="turn-1",
        speaker_role="user",
        created_at="2026-09-26T00:00:00Z",
    )
    assert calls["n"] == 1
    assert parsed.surface == "commitment"


def test_unreadable_completion_falls_back(monkeypatch: pytest.MonkeyPatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-secret")

    def transport(url, headers, body, timeout):
        return 200, _completion("not json")

    reasoner = Reasoner(fixture_mode=False)
    reasoner.llm.transport = transport
    parsed = reasoner.reason(
        _snapshot(),
        text="I will send the proposal by Friday.",
        turn_id="turn-1",
        speaker_role="user",
        created_at="2026-09-26T00:00:00Z",
    )
    assert parsed.surface == "ambiguous"
    assert parsed.confidence == 0.0
    assert parsed.proposed_status == "AWAITING_CLARIFICATION"
    assert parsed.suggested_speech == "CLARIFY"


def test_transport_error_falls_back_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("VERALOCK_LLM_API_KEY", "sk-secret")

    def transport(url, headers, body, timeout):
        raise OSError("connection reset sk-secret")

    reasoner = Reasoner(fixture_mode=False)
    reasoner.llm.transport = transport
    with caplog.at_level("WARNING"):
        parsed = reasoner.reason(
            _snapshot(),
            text="maybe later",
            turn_id="turn-9",
            speaker_role="user",
            created_at="2026-09-26T00:00:00Z",
        )
    assert parsed.surface == "ambiguous"
    assert parsed.source_turn_ids == ["turn-9"]
    assert "sk-secret" not in caplog.text


def test_http_status_and_refusal_raise_without_key():
    client = OpenAICompatibleLLM(api_key="sk-secret", transport=lambda *args: (503, b"{}"))
    with pytest.raises(LLMAdapterError, match="LLM HTTP 503") as exc:
        client.complete("prompt")
    assert "sk-secret" not in str(exc.value)

    def refuse(url, headers, body, timeout):
        return 200, json.dumps(
            {"choices": [{"message": {"refusal": "no", "content": None}}]}
        ).encode()

    client.transport = refuse
    with pytest.raises(LLMAdapterError, match="refused"):
        client.complete("prompt")


def test_message_content_list_and_object():
    def as_list(url, headers, body, timeout):
        return 200, _completion([{"type": "text", "text": _ag()}])

    client = OpenAICompatibleLLM(api_key="sk-secret", transport=as_list)
    assert parse_ag(client.complete("prompt")).topic_id == "proposal-delivery"

    def as_object(url, headers, body, timeout):
        return 200, _completion(json.loads(_ag()))

    client.transport = as_object
    assert parse_ag(client.complete("prompt")).suggested_speech == "SILENT"


def test_empty_api_key_rejected():
    with pytest.raises(ValueError):
        OpenAICompatibleLLM(api_key="  ")


def test_urllib_transport_reads_success_and_hides_error_body(monkeypatch: pytest.MonkeyPatch):
    class _Response:
        status = 200

        def read(self) -> bytes:
            return b'{"choices":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=0: _Response())
    status, raw = urllib_transport("https://example.test/v1/chat/completions", {}, b"{}", 1)
    assert status == 200
    assert raw == b'{"choices":[]}'

    def unauthorized(request, timeout=0):
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            EmailMessage(),
            io.BytesIO(b'{"error":"bad key sk-secret"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", unauthorized)
    with pytest.raises(LLMAdapterError, match="LLM HTTP 401") as exc:
        urllib_transport(
            "https://example.test/v1/chat/completions",
            {"Authorization": "Bearer sk-secret"},
            b"{}",
            1,
        )
    assert "sk-secret" not in str(exc.value)

    def timed_out(request, timeout=0):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", timed_out)
    with pytest.raises(LLMAdapterError, match="timed out"):
        urllib_transport("https://example.test/v1/chat/completions", {}, b"{}", 1)
