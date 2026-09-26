# VeraLock

Hackathon monorepo. Brain owns `intelligence/` and `state/`. Vault owns `storage/`, `evidence/`, and `followup/`.

## Vault (persistence layer)

Owns commitment persistence, provenance, conditional fields, follow-up scheduling,
and demo time-warp. Mirrors the Brain commitment contract exactly for field names
and status enum; Vault may **add** columns but never rename Brain fields.

### Layout

```
shared/commitment_schema.py   # CommitmentStatus + Commitment (Brain contract)
storage/schema.sql            # conversations, turns, commitments, events
storage/db.py                 # connect + init_schema
storage/models.py             # row models
storage/repositories/         # ConversationRepo, TurnRepo, CommitmentRepo, InterventionRepo
evidence/provenance.py        # get_commitment_evidence(commitment_id)
followup/scheduler.py         # due follow-ups vs (warped) now
followup/messages.py          # template messages (no LLM)
followup/time_warp.py         # FAST-FORWARD 48h override of now
scripts/smoke_vault.py        # end-to-end smoke test
```

### Status enum (UPPER_SNAKE)

`NO_COMMITMENT`, `DETECTED`, `AWAITING_CLARIFICATION`, `CONFIRMED`,
`UNRESOLVED_AMBIGUOUS`, `SUPERSEDED`, `WITHDRAWN`

### Brain fields (never rename)

`commitment_id`, `topic_id`, `status`, `speaker_role`, `canonical_text`, `raw_span`,
`source_turn_ids`, `clarification_count`, `intervened`, `is_acknowledgement`,
`is_intention_only`, `confidence`, `conditions`, `created_at`, `updated_at`, `session_id`

### Vault-only extras on commitments

`condition`, `dependency_owner`, `condition_status` (`PENDING`|`MET`|`FAILED`|`UNKNOWN`),
`due_at`, `next_followup_at`, `superseded_by_commitment_id`

Every commitment and every event stores `source_turn_ids` (JSON) for provenance.
`CommitmentRepo.update_status` always appends a `commitment_events` row.

### Smoke test

```bash
python scripts/smoke_vault.py
```

Expects exit 0, prints the evidence chain, and shows a follow-up becoming due after
FAST-FORWARD 48 hours.

### Not owned by Vault

Do **not** implement under Vault: `intelligence/`, `state/`, `voice/`, `frontend/`.

## Brain (intelligence + state)

Brain decides what a text turn means and whether VeraLock should speak.
It imports `Commitment` and `CommitmentStatus` from `shared/commitment_schema.py`
and does not rename those fields. It does not write Vault tables and it does
not depend on voice.

Clear commitment → `CONFIRMED` and speech `SILENT`.
An acknowledgement is not a commitment. An intention is not a commitment.
`intervened=true` blocks automatic confirmation.
At most one clarification question per `topic_id`. If the topic is still
ambiguous after that question, status becomes `UNRESOLVED_AMBIGUOUS` and
speech goes `SILENT`.

### Layout

```
intelligence/turn_filter.py       # cost/latency gate only; no semantic verdict
intelligence/context.py           # last 6 turns, active commitments, speaker roles
intelligence/reasoner.py          # one-shot structured A→G; StubLLM fixture mode
intelligence/guardrails.py        # ack, intention, intervened, clarification cap
intelligence/clarification.py     # max one question per topic_id
intelligence/pipeline.py          # wires the pieces; speech_action SILENT | CLARIFY
intelligence/api.py               # POST /v1/turn (FastAPI-compatible)
state/commitment_machine.py       # legal status transitions; illegal hops raise
```

A→G is a single JSON object: actor, topic bind, surface class, canonical draft,
evidence, flags, speech hint. The stub model is deterministic and uses no API
key. Guardrails, not the model, set the final status and speech action.

### Tests

```bash
pytest
```

No API keys. Fixture mode is the default (`BrainPipeline()`).

### Text turn entrypoint

```bash
pip install fastapi uvicorn
uvicorn intelligence.api:create_app --factory --port 8000
```

`POST /v1/turn` accepts `session_id`, `turn_id`, `speaker_role`, `text`, and
optional `intervened`, `created_at`, and `active_commitments`.
`GET /health` returns the Brain component check.

`handle_turn_payload` in `intelligence/api.py` is the same entry without HTTP.

