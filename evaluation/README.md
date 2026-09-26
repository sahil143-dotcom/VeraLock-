# Evaluation

Golden dialogs score Brain behavior. Each file in `scenarios/` is one session.
The harness builds `BrainPipeline(fixture_mode=True)`, so the reasoner is
`StubLLM`: deterministic, offline, no API key. It does not invent a second
model stub.

This package scores `TurnResult` (status, speech, flags). It does not write
Vault tables. A later Brain+Vault `PersistHandoff` end-to-end suite can reuse
the same scenarios through the hooks below.

## Run

From the repo root:

```bash
pytest evaluation/
```

Or the full suite (`tests/` and `evaluation/`):

```bash
pytest
```

Print a pass/fail table (scenario, turn, expected vs actual speech and status) and exit non-zero on any mismatch:

```bash
python scripts/run_eval.py
python -m evaluation
```

## Scenario format

YAML (or JSON) with these fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Stable name. Must match the filename stem. |
| `title` | yes | Short label shown in the report. |
| `description` | yes | Which product rule this dialog locks. |
| `session_id` | yes | Passed on every `TurnInput`. |
| `turns` | yes | Ordered turns. One `BrainPipeline` runs the whole list. |

Each turn:

| Field | Required | Meaning |
| --- | --- | --- |
| `turn_id` | yes | Unique within the scenario. |
| `speaker_role` | yes | Usually `user`. |
| `text` | yes | Utterance. Use phrases `StubLLM` already classifies. |
| `created_at` | no | ISO-8601 UTC. Defaults to `2026-09-26T00:00:00Z` plus the turn index in minutes. |
| `intervened` | no | Defaults to false. |
| `expect` | yes | Fields to score. Omitted fields are not compared. |

`expect` fields:

| Field | Meaning |
| --- | --- |
| `speech_action` | `SILENT` or `CLARIFY`. Required. |
| `status` | `CommitmentStatus` value on the commitment. `null` means no commitment (a filter skip must not invent `NO_COMMITMENT`). Omit the key to skip the check. |
| `skipped_by_filter` | Whether the cost/latency gate dropped the turn. |
| `filter_reason` | Gate reason: `empty`, `filler`, `duplicate`, or `pass`. |
| `clarification_count` | Count stored on the commitment. The one-question cap stays at 1. |
| `is_acknowledgement` | Flag on the commitment. |
| `is_intention_only` | Flag on the commitment. |
| `intervened` | Flag on the commitment (not the turn input). |
| `clarification_question` | Exact question, or `null` when speech must not ask. |
| `clarification_question_contains` | Substring of the question. |
| `same_commitment_as` | Earlier `turn_id` that must share `commitment_id`. |

Unknown keys are load errors. A misspelled expectation cannot pass silently.

State carries on the pipeline: clarification counts and active commitments
stay in memory for the session. Do not copy `active_commitments` from one
turn to the next inside a file. That matches the multi-turn tests in
`tests/test_policy.py`.

## Product rules these dialogs lock

1. Clear commitment → `speech_action` `SILENT` and status `CONFIRMED` (`clear_commitment.yaml`).
2. Acknowledgement is not a commitment. Intention is not a commitment. Both are `NO_COMMITMENT` and `SILENT`, with the matching flag (`acknowledgement.yaml`, `intention.yaml`).
3. `intervened: true` never auto-confirms. Expect `DETECTED`, not `CONFIRMED` (`intervened_blocks_confirm.yaml`).
4. At most one clarification per `topic_id`. The next still-ambiguous turn is `UNRESOLVED_AMBIGUOUS` and `SILENT`, and `clarification_count` stays 1 (`clarification_then_unresolved.yaml`). A clear reply after the question confirms and keeps that count (`clarification_then_confirm.yaml`).
5. Empty text, filler (`um`), and an immediate same-speaker duplicate are filter skips: `SILENT`, `skipped_by_filter`, and no commitment (`filter_skip.yaml`). Phrases match `intelligence/turn_filter.py` and `tests/test_context_and_filter.py`.

Phrase the text the way `tests/test_policy.py` does. `StubLLM` classifies with
the patterns in `intelligence/reasoner.py` (`classify_surface`). A sentence
the stub treats as ambiguous will clarify, even if it reads like a promise
to a person.

## Add a scenario

1. Copy an existing file in `evaluation/scenarios/`.
2. Set `id` to the filename stem (`my_dialog.yaml` → `id: my_dialog`).
3. Keep one `session_id` and at least two turns when the rule depends on memory.
4. Put only the fields you mean to lock under `expect`.
5. Run `pytest evaluation/test_golden.py -k my_dialog`.

The catalog test requires the rule files above to stay present. A new
file is picked up automatically.

Turn `text` is kept verbatim, including whitespace. `"   "` is the empty
skip used by the gate tests. Other fields are stripped.

## Extension: Brain + Vault PersistHandoff

Golden tests call `run_scenario(scenario)` with the default factory. They
assert `TurnResult` only.

`run_scenario` and `evaluate` take:

- `pipeline_factory(scenario) -> BrainPipeline` — build the pipeline yourself when a sink must observe the turn. Fixture mode stays on.
- `observers` — objects with `after_turn(scenario, turn, pipeline, result) -> list[str]`. Each string is a failure.

A later end-to-end test can pass a factory that sets `persist` to
`intelligence.persist.RecordingPersistSink` or Vault's `SqlitePersistSink`,
then an observer that checks the handoff (`speech_action`,
`transition.to_status`, `source_turn_ids`). Those checks stay out of the
golden YAML. Brain packages must not import `evaluation` (see the AST guard
in `tests/test_policy.py`).
