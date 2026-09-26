"""Single-shot structured reasoner (A→G).

One prompt, one completion, one JSON object with keys A through G.
`StubLLM` is deterministic and never reads API keys or the network.
`Reasoner(fixture_mode=True)` always uses that stub so tests stay offline.

`fixture_mode=False` with no injected client uses an OpenAI-compatible chat
model when `VERALOCK_LLM_API_KEY` or `OPENAI_API_KEY` is set, and falls
back to `StubLLM` when neither key is present.

The trace is a proposal. Guardrails choose the status and the speech action.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Protocol

from intelligence.context import ContextSnapshot
from intelligence.llm_adapter import LLMAdapterError, client_from_env
from intelligence.models import SILENT, ReasonerOutput, Surface
from intelligence.turn_filter import normalize
from shared.commitment_schema import Commitment, CommitmentStatus

logger = logging.getLogger(__name__)

_INPUT_RE = re.compile(r"<brain_input>\s*(.*?)\s*</brain_input>", re.DOTALL)

_ACK = frozenset(
    {
        "ok",
        "okay",
        "k",
        "got it",
        "gotcha",
        "sounds good",
        "thanks",
        "thank you",
        "sure",
        "yup",
        "yep",
        "noted",
        "cool",
        "alright",
        "all right",
        "understood",
        "roger",
        "copy",
    }
)

_UNRELATED = frozenset(
    {
        "hello",
        "hi",
        "hey",
        "good morning",
        "good afternoon",
        "good evening",
        "lol",
        "haha",
    }
)

_INTENTION_RE = re.compile(
    r"\b("
    r"i might|i may|i'll try|i will try|i'm going to try|i am going to try|"
    r"i plan to|i intend to|i'm thinking|i am thinking|i want to|"
    r"hopefully|i hope to|i'd like to|i would like to"
    r")\b"
)

_CLEAR_RE = re.compile(
    r"\b(i will|i'll|i commit to|i promise to|you can count on me to)\b"
)

_HEDGE_RE = re.compile(
    r"\b("
    r"try|trying|maybe|might|perhaps|soon|later|sometime|eventually|"
    r"something|somehow|probably|possibly|unsure|not sure"
    r")\b"
)

_VAGUE_RE = re.compile(r"\b(handle it|take care of it|deal with it|sort it|do it)\b")

_STOP = frozenset(
    {
        "i",
        "will",
        "i'll",
        "commit",
        "to",
        "promise",
        "you",
        "can",
        "count",
        "on",
        "me",
        "the",
        "a",
        "an",
        "by",
        "and",
        "of",
        "for",
        "my",
        "we",
        "our",
    }
)

_TRAIL_PUNCT = re.compile(r"[.!?]+$")


class LLMClient(Protocol):
    def complete(self, prompt: str) -> str:
        """Return one completion string. Implementations must be single-shot."""


def _strip_punct(text: str) -> str:
    return _TRAIL_PUNCT.sub("", normalize(text)).strip()


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9']+", _strip_punct(text))
    return [w for w in words if w not in _STOP]


def _canonical(text: str) -> str:
    raw = text.strip()
    body = re.sub(
        r"(?i)^(i will|i'll|i commit to|i promise to|you can count on me to)\s+",
        "",
        raw,
    ).strip()
    body = body.rstrip(".!?").strip()
    if not body:
        body = raw.rstrip(".!?").strip()
    if body:
        body = body[0].upper() + body[1:]
    if body and not body.endswith("."):
        body = body + "."
    return body or raw


def infer_topic(
    text: str,
    active: list[Commitment],
) -> tuple[str, Optional[str]]:
    """Bind to an active commitment when its topic phrase appears, else slug the text."""
    norm = _strip_punct(text)
    for commitment in active:
        phrase = commitment.topic_id.replace("-", " ").strip()
        if phrase and phrase in norm:
            return commitment.topic_id, commitment.commitment_id
    if "budget" in norm and "review" in norm:
        return "budget-review", None
    if "proposal" in norm or "friday" in norm:
        return "proposal-delivery", None
    words = _content_words(norm)
    if not words:
        return "untopiced", None
    return "-".join(words[:3]), None


def classify_surface(text: str) -> Surface:
    norm = _strip_punct(text)
    if norm in _ACK:
        return "acknowledgement"
    if norm in _UNRELATED:
        return "unrelated"
    if _INTENTION_RE.search(norm):
        return "intention"
    if _CLEAR_RE.search(norm) and not _HEDGE_RE.search(norm) and not _VAGUE_RE.search(norm):
        if len(_content_words(norm)) >= 2:
            return "commitment"
    if norm:
        return "ambiguous"
    return "unrelated"


def _proposal_for(surface: Surface) -> tuple[CommitmentStatus, str, bool, bool, Optional[str]]:
    """Status proposal and speech hint. Guardrails may override both."""
    if surface == "acknowledgement":
        return CommitmentStatus.NO_COMMITMENT, SILENT, True, False, None
    if surface == "intention":
        return CommitmentStatus.NO_COMMITMENT, SILENT, False, True, None
    if surface == "commitment":
        return CommitmentStatus.CONFIRMED, SILENT, False, False, None
    if surface == "unrelated":
        return CommitmentStatus.NO_COMMITMENT, SILENT, False, False, None
    return CommitmentStatus.AWAITING_CLARIFICATION, "CLARIFY", False, False, None


def build_ag(
    *,
    speaker_role: str,
    text: str,
    turn_id: str,
    active: list[Commitment],
) -> dict[str, Any]:
    surface = classify_surface(text)
    topic_id, matched_id = infer_topic(text, active)
    if surface == "acknowledgement":
        topic_id, matched_id = "acknowledgement", None
    elif surface == "unrelated":
        topic_id, matched_id = "unrelated", None
    status, speech, is_ack, is_intention, _question = _proposal_for(surface)
    canonical = text.strip() if surface in {"acknowledgement", "intention", "unrelated"} else _canonical(text)
    confidence = {
        "acknowledgement": 0.95,
        "intention": 0.86,
        "commitment": 0.93,
        "ambiguous": 0.42,
        "unrelated": 0.9,
    }[surface]
    question = None
    if speech == "CLARIFY":
        # Wording is finalized by clarification.build_question in guardrails
        # so the model hint and the ledger stay aligned. Leave the hint empty
        # here; G.suggested_speech still says CLARIFY.
        question = None
    return {
        "A": {"actor": speaker_role},
        "B": {"topic_id": topic_id, "matched_commitment_id": matched_id},
        "C": {"surface": surface},
        "D": {"canonical_text": canonical, "raw_span": text.strip()},
        "E": {
            "confidence": confidence,
            "conditions": None,
            "source_turn_ids": [turn_id],
        },
        "F": {
            "is_acknowledgement": is_ack,
            "is_intention_only": is_intention,
            "proposed_status": status.value,
        },
        "G": {"suggested_speech": speech, "clarification_question": question},
    }


def render_prompt(snapshot: ContextSnapshot, turn_text: str, turn_id: str, speaker_role: str, created_at: str) -> str:
    """One prompt. The model must answer with a single A→G JSON object."""
    payload = {
        "session_id": snapshot.session_id,
        "speaker_roles": list(snapshot.speaker_roles),
        "turn": {
            "turn_id": turn_id,
            "speaker_role": speaker_role,
            "text": turn_text,
            "created_at": created_at,
        },
        "recent_turns": [
            {
                "turn_id": t.turn_id,
                "speaker_role": t.speaker_role,
                "text": t.text,
                "created_at": t.created_at,
            }
            for t in snapshot.turns
        ],
        "active_commitments": [
            {
                "commitment_id": c.commitment_id,
                "topic_id": c.topic_id,
                "status": c.status.value if isinstance(c.status, CommitmentStatus) else c.status,
                "speaker_role": c.speaker_role,
                "canonical_text": c.canonical_text,
                "clarification_count": c.clarification_count,
                "intervened": c.intervened,
            }
            for c in snapshot.active_commitments
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=True)
    return (
        "You are VeraLock Brain. Reason about ONE turn in a single shot.\n"
        "Return one JSON object and nothing else, with keys A, B, C, D, E, F, G:\n"
        "A actor: speaker_role of this turn.\n"
        "B bind: topic_id and matched_commitment_id (or null).\n"
        "C classify surface: acknowledgement | intention | commitment | ambiguous | unrelated.\n"
        "D draft: canonical_text and raw_span.\n"
        "E evidence: confidence, conditions, source_turn_ids.\n"
        "F flags: is_acknowledgement, is_intention_only, proposed_status "
        "(NO_COMMITMENT | DETECTED | AWAITING_CLARIFICATION | CONFIRMED | UNRESOLVED_AMBIGUOUS).\n"
        "G speech hint: suggested_speech SILENT or CLARIFY, plus clarification_question or null.\n"
        "Rules you must respect in the proposal: an acknowledgement is not a commitment; "
        "an intention is not a commitment; a clear commitment suggests CONFIRMED and SILENT; "
        "an ambiguous commitment suggests AWAITING_CLARIFICATION and CLARIFY.\n"
        "Do not apply policy overrides. Guardrails do that after you.\n"
        "<brain_input>\n"
        f"{encoded}\n"
        "</brain_input>\n"
    )


def parse_ag(raw: str) -> ReasonerOutput:
    """Parse a single A→G JSON object. Raises ValueError on a malformed trace."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    for key in ("A", "B", "C", "D", "E", "F", "G"):
        if key not in data or not isinstance(data[key], dict):
            raise ValueError(f"A→G missing object {key}")
    surface = data["C"]["surface"]
    if surface not in {"acknowledgement", "intention", "commitment", "ambiguous", "unrelated"}:
        raise ValueError(f"unknown surface: {surface}")
    speech = data["G"]["suggested_speech"]
    if speech not in {"SILENT", "CLARIFY"}:
        raise ValueError(f"unknown suggested_speech: {speech}")
    # Validate the enum without accepting unknown statuses.
    CommitmentStatus(data["F"]["proposed_status"])
    confidence = float(data["E"]["confidence"])
    conditions = data["E"].get("conditions")
    if conditions is not None and not isinstance(conditions, dict):
        raise ValueError("conditions must be an object or null")
    source_ids = [str(x) for x in data["E"].get("source_turn_ids") or []]
    matched = data["B"].get("matched_commitment_id")
    return ReasonerOutput(
        actor=str(data["A"]["actor"]),
        topic_id=str(data["B"]["topic_id"]),
        matched_commitment_id=str(matched) if matched else None,
        surface=surface,
        canonical_text=str(data["D"]["canonical_text"]),
        raw_span=data["D"].get("raw_span"),
        confidence=confidence,
        conditions=conditions,
        source_turn_ids=source_ids,
        is_acknowledgement=bool(data["F"]["is_acknowledgement"]),
        is_intention_only=bool(data["F"]["is_intention_only"]),
        proposed_status=str(data["F"]["proposed_status"]),
        suggested_speech=speech,
        clarification_question=data["G"].get("clarification_question"),
        ag=data,
    )


def _fallback(speaker_role: str, text: str, turn_id: str) -> ReasonerOutput:
    """Unparseable model output becomes ambiguous, never a confirmation."""
    ag = {
        "A": {"actor": speaker_role},
        "B": {"topic_id": "untopiced", "matched_commitment_id": None},
        "C": {"surface": "ambiguous"},
        "D": {"canonical_text": _canonical(text), "raw_span": text.strip()},
        "E": {"confidence": 0.0, "conditions": None, "source_turn_ids": [turn_id]},
        "F": {
            "is_acknowledgement": False,
            "is_intention_only": False,
            "proposed_status": CommitmentStatus.AWAITING_CLARIFICATION.value,
        },
        "G": {"suggested_speech": "CLARIFY", "clarification_question": None},
    }
    parsed = parse_ag(json.dumps(ag))
    return parsed


class StubLLM:
    """Deterministic fixture model. No network. No API key."""

    def complete(self, prompt: str) -> str:
        match = _INPUT_RE.search(prompt)
        if not match:
            raise ValueError("stub LLM prompt is missing <brain_input>")
        payload = json.loads(match.group(1))
        turn = payload["turn"]
        active_raw = payload.get("active_commitments") or []
        active: list[Commitment] = []
        for item in active_raw:
            active.append(
                Commitment(
                    commitment_id=item["commitment_id"],
                    topic_id=item["topic_id"],
                    status=CommitmentStatus(item["status"]),
                    speaker_role=item.get("speaker_role") or "user",
                    canonical_text=item.get("canonical_text") or "",
                    raw_span=None,
                    source_turn_ids=[],
                    clarification_count=int(item.get("clarification_count") or 0),
                    intervened=bool(item.get("intervened")),
                    is_acknowledgement=False,
                    is_intention_only=False,
                    confidence=0.0,
                    conditions=None,
                    created_at="",
                    updated_at="",
                    session_id=payload.get("session_id"),
                )
            )
        ag = build_ag(
            speaker_role=turn["speaker_role"],
            text=turn["text"],
            turn_id=turn["turn_id"],
            active=active,
        )
        return json.dumps(ag)


class Reasoner:
    """Single-shot A→G.

    Fixture mode (the default) forces `StubLLM` and ignores both an injected
    client and any API key in the environment. With `fixture_mode=False`, an
    injected client wins; otherwise a key selects `OpenAICompatibleLLM` and a
    missing key selects `StubLLM`.
    """

    def __init__(self, llm: Optional[LLMClient] = None, *, fixture_mode: bool = True) -> None:
        self.fixture_mode = fixture_mode
        if fixture_mode:
            self.llm: LLMClient = StubLLM()
        elif llm is not None:
            self.llm = llm
        else:
            self.llm = client_from_env() or StubLLM()

    def reason(
        self,
        snapshot: ContextSnapshot,
        *,
        text: str,
        turn_id: str,
        speaker_role: str,
        created_at: str,
    ) -> ReasonerOutput:
        prompt = render_prompt(snapshot, text, turn_id, speaker_role, created_at)
        # The snapshot's recent_turns list is already capped at 6 by ContextWindow.
        try:
            raw = self.llm.complete(prompt)
        except LLMAdapterError as exc:
            logger.warning("Brain LLM request failed; using ambiguous fallback: %s", exc)
            return _fallback(speaker_role, text, turn_id)
        try:
            return parse_ag(raw)
        except (ValueError, json.JSONDecodeError, KeyError, TypeError):
            return _fallback(speaker_role, text, turn_id)
