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
evidence/api.py               # GET /v1/evidence/{id} (FastAPI for Glass)
scripts/smoke_evidence_api.py
followup/scheduler.py         # due follow-ups vs (warped) now
followup/messages.py          # template messages (no LLM)
followup/time_warp.py         # FAST-FORWARD 48h override of now
scripts/smoke_vault.py        # end-to-end smoke test
shared/persist_handoff.py     # PersistHandoff + PersistPort (Brain→Vault)
storage/persist_sink.py       # SqlitePersistSink (Vault PersistPort)
scripts/smoke_persist_handoff.py
```

### Brain → Vault persist handoff

Brain emits a `PersistHandoff` (see `shared/persist_handoff.py`) after each
`TurnResult`. Vault implements `PersistPort` via `SqlitePersistSink`, which
ensures conversation/turn rows and upserts commitments through the existing
repos. Brain never imports `storage/`; default sink is `NullPersistPort`.


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

### Evidence HTTP (for Glass)

Thin FastAPI surface over `get_commitment_evidence` so Glass can fetch
commitment provenance without importing Vault Python in-process.

```bash
pip install fastapi uvicorn httpx
# with an existing SQLite path:
export VERALOCK_VAULT_DB=/path/to/vault.db
uvicorn evidence.api:app --port 8001
# or factory with explicit path:
uvicorn evidence.api:create_evidence_app --factory --port 8001
```

- `GET /v1/health` → `{"status":"ok","component":"vault-evidence"}`
- `GET /v1/evidence/{commitment_id}` → JSON with `commitment`, `events`,
  `intervention_events`, `source_turns` (plain dicts). 404 if missing.

```bash
python scripts/smoke_evidence_api.py
```

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
intelligence/persist.py           # maps a turn onto shared.build_handoff
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
optional `intervened`, `created_at`, `active_commitments`, `conversation_id`,
and `intervention_reason`.
`GET /health` returns the Brain component check.

`handle_turn_payload` in `intelligence/api.py` is the same entry without HTTP.

### Persist handoff

After every turn, including filter skips, `BrainPipeline` asks
`intelligence/persist.py` for a handoff and calls `PersistPort.persist`.
That module imports `Transition`, `PersistHandoff`, `PersistPort`,
`NullPersistPort`, and `build_handoff` from `shared/persist_handoff.py`.
Brain does not write `storage/`. The default port is `NullPersistPort`.
`POST /v1/turn` still returns only the `TurnResult` dict.

`Transition.to_status` is always a string. Filter skips send `commitment=null`,
`from_status=null`, and `to_status=NO_COMMITMENT`. An acknowledgement may
include a commitment dict whose status is `NO_COMMITMENT`; `to_status` is then
`NO_COMMITMENT`. A non-null commitment has a non-empty `source_turn_ids` that
includes `turn_id`, and `to_status` equals `commitment.status`.
`intervention_reason` is set only when `intervened_this_turn` is true.
`RecordingPersistSink` in `intelligence/persist.py` records handoffs for tests.

```python
from intelligence.api import create_app, get_pipeline
from intelligence.persist import RecordingPersistSink
from intelligence.pipeline import BrainPipeline

sink = RecordingPersistSink()  # Vault supplies SqlitePersistSink in production
pipeline = BrainPipeline(persist=sink)  # default is NullPersistPort
app = create_app(persist=sink)
get_pipeline(persist=sink)
```

