"""Brain policy tests. No API keys; fixture mode is deterministic."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from intelligence.models import CLARIFY, SILENT, ReasonerOutput
from intelligence.pipeline import BrainPipeline, TurnInput
from intelligence.reasoner import parse_ag, render_prompt
from intelligence.context import ContextSnapshot
from intelligence.guardrails import decide
from shared.commitment_schema import Commitment, CommitmentStatus

BRAIN_FIELDS = [
    "commitment_id",
    "topic_id",
    "status",
    "speaker_role",
    "canonical_text",
    "raw_span",
    "source_turn_ids",
    "clarification_count",
    "intervened",
    "is_acknowledgement",
    "is_intention_only",
    "confidence",
    "conditions",
    "created_at",
    "updated_at",
    "session_id",
]


def turn(text: str, **kwargs) -> TurnInput:
    return TurnInput(
        session_id=kwargs.get("session_id", "sess-1"),
        turn_id=kwargs.get("turn_id", "turn-1"),
        speaker_role=kwargs.get("speaker_role", "user"),
        text=text,
        created_at=kwargs.get("created_at", "2026-09-26T00:00:00Z"),
        intervened=kwargs.get("intervened", False),
        active_commitments=kwargs.get("active_commitments") or [],
    )


def make_commitment(status: CommitmentStatus, **overrides) -> Commitment:
    now = "2026-09-26T00:00:00Z"
    base = dict(
        commitment_id="cmt-1",
        topic_id="proposal-delivery",
        status=status,
        speaker_role="user",
        canonical_text="Send the proposal by Friday.",
        raw_span="I will send the proposal by Friday.",
        source_turn_ids=["turn-0"],
        clarification_count=0,
        intervened=False,
        is_acknowledgement=False,
        is_intention_only=False,
        confidence=0.9,
        conditions=None,
        created_at=now,
        updated_at=now,
        session_id="sess-1",
    )
    base.update(overrides)
    return Commitment(**base)


def test_shared_schema_fields_unchanged():
    names = list(Commitment.__dataclass_fields__)
    assert names[: len(BRAIN_FIELDS)] == BRAIN_FIELDS
    assert CommitmentStatus.NO_COMMITMENT.value == "NO_COMMITMENT"
    assert CommitmentStatus.CONFIRMED.value == "CONFIRMED"
    assert CommitmentStatus.UNRESOLVED_AMBIGUOUS.value == "UNRESOLVED_AMBIGUOUS"


def test_brain_imports_shared_types_not_copies():
    import intelligence.guardrails as guardrails
    import intelligence.pipeline as pipeline
    import shared.commitment_schema as schema
    import state.commitment_machine as machine

    assert pipeline.Commitment is schema.Commitment
    assert pipeline.CommitmentStatus is schema.CommitmentStatus
    assert machine.Commitment is schema.Commitment
    assert guardrails.CommitmentStatus is schema.CommitmentStatus
    # Brain + shared contract must not import Vault/UI packages.
    # AST check on sources (not global sys.modules) so Vault unit tests can
    # load storage without poisoning this assertion.
    forbidden = {"storage", "evidence", "followup", "voice", "frontend", "evaluation"}
    root = Path(__file__).resolve().parents[1]
    for pkg in ("intelligence", "state", "shared"):
        for py in (root / pkg).rglob("*.py"):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        assert top not in forbidden, f"{py.relative_to(root)} imports {alias.name}"
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    assert top not in forbidden, f"{py.relative_to(root)} imports {node.module}"

@pytest.mark.parametrize("text", ["ok", "Got it.", "sounds good", "thanks", "OK!"])
def test_ack_is_no_commitment_and_silent(text: str):
    result = BrainPipeline().handle_turn(turn(text))
    assert result.speech_action == SILENT
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.NO_COMMITMENT
    assert result.commitment.is_acknowledgement is True
    assert result.commitment.is_intention_only is False
    assert result.clarification_question is None
    assert result.skipped_by_filter is False


def test_intention_is_not_a_commitment():
    result = BrainPipeline().handle_turn(
        turn("I might send the proposal by Friday.", turn_id="turn-intent")
    )
    assert result.speech_action == SILENT
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.NO_COMMITMENT
    assert result.commitment.is_intention_only is True
    assert result.commitment.is_acknowledgement is False


@pytest.mark.parametrize(
    "text",
    [
        "I will send the proposal by Friday.",
        "I'll send the proposal by Friday.",
    ],
)
def test_clear_commitment_is_confirmed_and_silent(text: str):
    result = BrainPipeline().handle_turn(turn(text))
    assert result.speech_action == SILENT
    assert result.clarification_question is None
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.CONFIRMED
    assert result.commitment.is_acknowledgement is False
    assert result.commitment.is_intention_only is False
    assert result.commitment.intervened is False
    assert result.commitment.topic_id == "proposal-delivery"
    assert result.reasoning is not None
    assert set(result.reasoning.ag) == {"A", "B", "C", "D", "E", "F", "G"}


def test_clarification_then_unresolved_ambiguous_on_same_pipeline():
    pipeline = BrainPipeline()
    first = pipeline.handle_turn(
        turn("I'll handle the budget review soon.", turn_id="turn-a")
    )
    assert first.speech_action == CLARIFY
    assert first.commitment is not None
    assert first.commitment.status == CommitmentStatus.AWAITING_CLARIFICATION
    assert first.commitment.clarification_count == 1
    assert first.clarification_question
    assert "budget-review" in first.clarification_question

    second = pipeline.handle_turn(
        turn(
            "Maybe the budget review sometime, I'm not sure.",
            turn_id="turn-b",
            created_at="2026-09-26T00:01:00Z",
        )
    )
    assert second.speech_action == SILENT
    assert second.clarification_question is None
    assert second.commitment is not None
    assert second.commitment.status == CommitmentStatus.UNRESOLVED_AMBIGUOUS
    assert second.commitment.clarification_count == 1
    assert second.commitment.commitment_id == first.commitment.commitment_id
    assert "clarification_cap" in second.policy_notes

    third = pipeline.handle_turn(
        turn(
            "Still fuzzy on the budget review.",
            turn_id="turn-c",
            created_at="2026-09-26T00:02:00Z",
        )
    )
    assert third.speech_action == SILENT
    assert third.clarification_question is None
    assert third.commitment is not None
    assert third.commitment.status == CommitmentStatus.UNRESOLVED_AMBIGUOUS
    assert third.commitment.clarification_count == 1


def test_clarification_cap_is_per_session():
    pipeline = BrainPipeline()
    first = pipeline.handle_turn(
        turn("I'll handle the budget review soon.", turn_id="s1", session_id="sess-a")
    )
    assert first.speech_action == CLARIFY
    other = pipeline.handle_turn(
        turn(
            "I'll handle the budget review soon.",
            turn_id="s2",
            session_id="sess-b",
            created_at="2026-09-26T00:04:00Z",
        )
    )
    assert other.speech_action == CLARIFY
    assert other.commitment is not None
    assert other.commitment.status == CommitmentStatus.AWAITING_CLARIFICATION
    assert other.commitment.clarification_count == 1


def test_stateless_client_passes_clarification_count():
    first = BrainPipeline().handle_turn(
        turn("I'll handle the budget review soon.", turn_id="turn-a", session_id="sess-a")
    )
    assert first.commitment is not None
    second = BrainPipeline().handle_turn(
        turn(
            "Maybe the budget review sometime, I'm not sure.",
            turn_id="turn-b",
            session_id="sess-b",
            active_commitments=[first.commitment],
        )
    )
    assert second.speech_action == SILENT
    assert second.commitment is not None
    assert second.commitment.status == CommitmentStatus.UNRESOLVED_AMBIGUOUS
    assert second.clarification_question is None


def test_clear_reply_after_clarification_confirms():
    pipeline = BrainPipeline()
    first = pipeline.handle_turn(
        turn("I'll handle the budget review soon.", turn_id="turn-a")
    )
    assert first.speech_action == CLARIFY
    second = pipeline.handle_turn(
        turn(
            "I will finish the budget review by Friday.",
            turn_id="turn-b",
            created_at="2026-09-26T00:03:00Z",
        )
    )
    assert second.speech_action == SILENT
    assert second.commitment is not None
    assert second.commitment.status == CommitmentStatus.CONFIRMED
    assert second.commitment.commitment_id == first.commitment.commitment_id


def test_intervened_flag_on_turn_blocks_auto_confirm():
    result = BrainPipeline().handle_turn(
        turn("I will send the proposal by Friday.", intervened=True)
    )
    assert result.speech_action == SILENT
    assert result.clarification_question is None
    assert result.commitment is not None
    assert result.commitment.status != CommitmentStatus.CONFIRMED
    assert result.commitment.status == CommitmentStatus.DETECTED
    assert result.commitment.intervened is True
    assert "blocked_auto_confirm" in result.policy_notes


def test_existing_intervened_commitment_blocks_auto_confirm():
    existing = make_commitment(
        CommitmentStatus.DETECTED,
        intervened=True,
        canonical_text="Send the proposal by Friday.",
    )
    result = BrainPipeline().handle_turn(
        turn(
            "I will send the proposal by Friday.",
            turn_id="turn-2",
            active_commitments=[existing],
        )
    )
    assert result.speech_action == SILENT
    assert result.commitment is not None
    assert result.commitment.commitment_id == existing.commitment_id
    assert result.commitment.status == CommitmentStatus.DETECTED
    assert result.commitment.intervened is True
    assert result.commitment.status != CommitmentStatus.CONFIRMED


def test_guardrails_override_model_speech_on_clear_commitment():
    proposal = ReasonerOutput(
        actor="user",
        topic_id="proposal-delivery",
        matched_commitment_id=None,
        surface="commitment",
        canonical_text="Send the proposal by Friday.",
        raw_span="I will send the proposal by Friday.",
        confidence=0.99,
        conditions=None,
        source_turn_ids=["turn-1"],
        is_acknowledgement=False,
        is_intention_only=False,
        proposed_status="CONFIRMED",
        suggested_speech=CLARIFY,
        clarification_question="Are you sure?",
        ag={},
    )
    policy = decide(proposal, existing=None, clarification_count=0, intervened=False)
    assert policy.status == CommitmentStatus.CONFIRMED
    assert policy.speech_action == SILENT
    assert policy.clarification_question is None


def test_guardrails_ack_beats_a_confirm_proposal():
    proposal = ReasonerOutput(
        actor="user",
        topic_id="proposal-delivery",
        matched_commitment_id=None,
        surface="acknowledgement",
        canonical_text="Got it.",
        raw_span="Got it.",
        confidence=0.4,
        conditions=None,
        source_turn_ids=["turn-1"],
        is_acknowledgement=True,
        is_intention_only=False,
        proposed_status="CONFIRMED",
        suggested_speech=CLARIFY,
        clarification_question="confirm?",
        ag={},
    )
    policy = decide(proposal, existing=None, clarification_count=0, intervened=False)
    assert policy.status == CommitmentStatus.NO_COMMITMENT
    assert policy.speech_action == SILENT
    assert policy.is_acknowledgement is True
    assert policy.apply_to_existing is False


def test_reasoner_single_shot_json_round_trip():
    pipeline = BrainPipeline()
    result = pipeline.handle_turn(turn("I will send the proposal by Friday."))
    assert result.reasoning is not None
    rendered = render_prompt(
        ContextSnapshot(
            session_id="sess-1",
            turns=[],
            active_commitments=[],
            speaker_roles=["user"],
        ),
        "I will send the proposal by Friday.",
        "turn-1",
        "user",
        "2026-09-26T00:00:00Z",
    )
    assert "<brain_input>" in rendered
    assert "recent_turns" in rendered
    parsed = parse_ag(pipeline.reasoner.llm.complete(rendered))
    assert parsed.surface == "commitment"
    assert parsed.proposed_status == "CONFIRMED"
    assert parsed.ag["G"]["suggested_speech"] == "SILENT"


def test_injected_llm_is_called_once_outside_fixture_mode():
    calls = {"n": 0}

    class OneShot:
        def complete(self, prompt: str) -> str:
            calls["n"] += 1
            assert prompt.count("<brain_input>") == 1
            return pipeline_llm_json()

    pipeline = BrainPipeline(fixture_mode=False, llm=OneShot())
    result = pipeline.handle_turn(turn("whatever the model says"))
    assert calls["n"] == 1
    assert result.commitment is not None
    assert result.commitment.status == CommitmentStatus.CONFIRMED
    assert result.speech_action == SILENT


def pipeline_llm_json() -> str:
    return """
    {
      "A": {"actor": "user"},
      "B": {"topic_id": "proposal-delivery", "matched_commitment_id": null},
      "C": {"surface": "commitment"},
      "D": {"canonical_text": "Send the proposal by Friday.", "raw_span": "I will send the proposal by Friday."},
      "E": {"confidence": 0.91, "conditions": null, "source_turn_ids": ["turn-1"]},
      "F": {"is_acknowledgement": false, "is_intention_only": false, "proposed_status": "CONFIRMED"},
      "G": {"suggested_speech": "SILENT", "clarification_question": null}
    }
    """
