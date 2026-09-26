"""Brain → Vault observer for golden dialogs.

Builds ``BrainPipeline(fixture_mode=True, persist=SqlitePersistSink(conn))``
and, after each turn, checks the SQLite row against ``TurnResult`` via
``evidence.provenance.get_commitment_evidence``.

One connection per scenario. Turn ids repeat across files (``turn-1``), so
scenarios must not share a database.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from evidence.provenance import get_commitment_evidence
from intelligence.models import TurnResult
from intelligence.pipeline import BrainPipeline
from storage.db import connect, init_schema
from storage.persist_sink import SqlitePersistSink

from evaluation.schema import Scenario, ScenarioTurn


def _status_name(value: object) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    text = str(value).strip()
    return text or None


@dataclass
class TurnObservation:
    """What Vault stored for one finished turn. ``None`` status means no commitment."""

    scenario_id: str
    turn_id: str
    speech_action: str | None
    status: str | None
    skipped_by_filter: bool
    commitment_id: str | None
    db_status: str | None
    source_turn_ids: list[str]
    turn_persisted: bool
    intervened: bool | None
    intervention_turn_ids: list[str]
    event_count: int


@dataclass
class PersistHandoffSession:
    """Factory + observer pair. ``after_turn`` returns harness failure strings."""

    connections: dict[str, sqlite3.Connection] = field(default_factory=dict)
    observations: list[TurnObservation] = field(default_factory=list)

    def factory(self, scenario: Scenario) -> BrainPipeline:
        conn = connect(":memory:")
        init_schema(conn)
        self.connections[scenario.id] = conn
        return BrainPipeline(fixture_mode=True, persist=SqlitePersistSink(conn))

    def connection_for(self, scenario_id: str) -> sqlite3.Connection:
        return self.connections[scenario_id]

    def observation(self, scenario_id: str, turn_id: str) -> TurnObservation:
        for item in self.observations:
            if item.scenario_id == scenario_id and item.turn_id == turn_id:
                return item
        raise KeyError(f"no observation for {scenario_id} {turn_id}")

    def after_turn(
        self,
        scenario: Scenario,
        turn: ScenarioTurn,
        pipeline: BrainPipeline,
        result: TurnResult,
    ) -> list[str]:
        del pipeline
        conn = self.connections[scenario.id]
        prefix = f"{scenario.id} {turn.turn_id}"
        failures: list[str] = []
        commitment = result.commitment
        status = _status_name(commitment.status) if commitment is not None else None
        commitment_id = commitment.commitment_id if commitment is not None else None

        turn_row = conn.execute(
            "SELECT turn_id, speaker_role, content FROM turns WHERE turn_id = ?",
            (turn.turn_id,),
        ).fetchone()
        turn_persisted = turn_row is not None
        if turn_row is None:
            failures.append(f"{prefix}: turn row was not persisted")
        else:
            if turn_row["content"] != turn.text:
                failures.append(
                    f"{prefix}: persisted turn content {turn_row['content']!r} "
                    f"!= utterance {turn.text!r}"
                )
            if turn_row["speaker_role"] != turn.speaker_role:
                failures.append(
                    f"{prefix}: persisted speaker_role {turn_row['speaker_role']!r} "
                    f"!= {turn.speaker_role!r}"
                )

        db_status: str | None = None
        source_turn_ids: list[str] = []
        intervened: bool | None = None
        intervention_turn_ids: list[str] = []
        event_count = 0

        if commitment is None:
            if not result.skipped_by_filter:
                failures.append(
                    f"{prefix}: commitment is null but skipped_by_filter is false"
                )
            cited = _commitment_ids_citing(conn, turn.turn_id)
            if cited:
                failures.append(
                    f"{prefix}: filter skip still wrote commitment(s) {cited}"
                )
        else:
            result_ids = [str(item) for item in commitment.source_turn_ids]
            if turn.turn_id not in result_ids:
                failures.append(
                    f"{prefix}: TurnResult source_turn_ids {result_ids} "
                    "does not include this turn"
                )
            try:
                evidence = get_commitment_evidence(commitment.commitment_id, conn)
            except KeyError as exc:
                failures.append(f"{prefix}: get_commitment_evidence failed: {exc}")
                evidence = None

            if evidence is not None:
                row = evidence["commitment"]
                db_status = _status_name(row.status)
                source_turn_ids = [str(item) for item in row.source_turn_ids]
                intervened = bool(row.intervened)
                event_count = len(evidence["events"])
                intervention_turn_ids = sorted(
                    {
                        str(turn_id)
                        for event in evidence["intervention_events"]
                        for turn_id in event.source_turn_ids
                    }
                )
                chain_turn_ids = [item.turn_id for item in evidence["source_turns"]]

                if db_status != status:
                    failures.append(
                        f"{prefix}: DB commitment status {db_status!r} "
                        f"!= TurnResult {status!r}"
                    )
                if turn.turn_id not in source_turn_ids:
                    failures.append(
                        f"{prefix}: evidence source_turn_ids {source_turn_ids} "
                        f"missing {turn.turn_id}"
                    )
                if turn.turn_id not in chain_turn_ids:
                    failures.append(
                        f"{prefix}: evidence source_turns {chain_turn_ids} "
                        f"missing {turn.turn_id}"
                    )
                if event_count == 0:
                    failures.append(f"{prefix}: evidence chain has no commitment_events")
                elif not any(event.source_turn_ids for event in evidence["events"]):
                    failures.append(
                        f"{prefix}: commitment_events are missing source_turn_ids"
                    )
                if intervened != bool(commitment.intervened):
                    failures.append(
                        f"{prefix}: DB intervened {intervened!r} "
                        f"!= TurnResult {bool(commitment.intervened)!r}"
                    )
                if bool(commitment.intervened):
                    if status == "CONFIRMED":
                        failures.append(
                            f"{prefix}: intervened commitment status is CONFIRMED"
                        )
                    if not evidence["intervention_events"]:
                        failures.append(
                            f"{prefix}: intervened commitment has no intervention_events"
                        )
                    elif turn.intervened and turn.turn_id not in intervention_turn_ids:
                        failures.append(
                            f"{prefix}: intervention evidence turns "
                            f"{intervention_turn_ids} missing {turn.turn_id}"
                        )

        self.observations.append(
            TurnObservation(
                scenario_id=scenario.id,
                turn_id=turn.turn_id,
                speech_action=result.speech_action,
                status=status,
                skipped_by_filter=bool(result.skipped_by_filter),
                commitment_id=commitment_id,
                db_status=db_status,
                source_turn_ids=source_turn_ids,
                turn_persisted=turn_persisted,
                intervened=intervened,
                intervention_turn_ids=intervention_turn_ids,
                event_count=event_count,
            )
        )
        return failures


def _commitment_ids_citing(conn: sqlite3.Connection, turn_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT commitment_id, source_turn_ids FROM commitments"
    ).fetchall()
    cited: list[str] = []
    for row in rows:
        raw = row["source_turn_ids"]
        try:
            ids = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            ids = []
        if isinstance(ids, list) and turn_id in [str(item) for item in ids]:
            cited.append(str(row["commitment_id"]))
    return cited
