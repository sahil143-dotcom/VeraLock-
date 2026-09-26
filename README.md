# VeraLock

Hackathon monorepo. Brain owns `intelligence/` and `state/`. Vault owns `storage/`, `evidence/`, and `followup/`. Voice owns `voice/`.

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
intelligence/persist.py           # maps TurnInput/TurnResult onto PersistHandoff
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

After every turn, including filter skips, `BrainPipeline` builds a
`PersistHandoff` and calls `PersistPort.persist`. The payload types
(`PersistHandoff`, `Transition`, `PersistPort`, `NullPersistPort`) live in
`shared/persist_handoff.py`. Brain imports that module and does not write
`storage/`. The default port is `NullPersistPort`. `POST /v1/turn` still
returns only the `TurnResult` dict; the handoff is a side effect.

`intelligence/persist.py` `build_handoff(turn_input, turn_result, ...)` enforces
the freeze rules: `session_id` is required; a non-null commitment has a
non-empty `source_turn_ids` that includes `turn_id`; when a commitment is
present, `transition.to_status` equals `commitment.status`. `from_status` is
the status before apply on an update, and null on create. Filter skips send
`commitment=null` and `to_status=NO_COMMITMENT`. `intervention_reason` is
optional.

```python
from intelligence.api import create_app, get_pipeline
from intelligence.persist import RecordingPersistSink
from intelligence.pipeline import BrainPipeline

sink = RecordingPersistSink()  # Vault supplies SqlitePersistSink at process start
pipeline = BrainPipeline(persist=sink)
app = create_app(persist=sink)
get_pipeline(persist=sink)
```

## Voice (audio → text turn)

Voice turns audio or plain text into the JSON body for `POST /v1/turn`.
Audio stays in Voice. Brain receives text only and does not import `voice/`.
Voice does not import Brain.

CI and demos use `StubASR` (canned transcript, no network, no API key).
`build_turn` is the text-first path: a string in, a turn body out, no ASR.

The optional OpenAI Whisper adapter runs only when both are set:

- `VERALOCK_VOICE_ASR=openai` or `VERALOCK_VOICE_ASR=whisper`
- `OPENAI_API_KEY`

If either is missing, `turn_audio_adapter_from_env()` stays on `StubASR`.
Optional overrides: `OPENAI_TRANSCRIBE_MODEL` (default `whisper-1`),
`OPENAI_BASE_URL`. No Whisper SDK is required for the default path.

### Layout

```
voice/turn_audio.py     # TurnAudioAdapter, StubASR, OpenAIWhisperAdapter
voice/turn_builder.py   # build_turn → {session_id, turn_id, speaker_role, text, created_at?}
voice/capture.py        # MicCapture, StreamCapture, FakeCapture (no live device)
voice/pipeline.py       # emit_text; optional capture → adapter → build_turn
scripts/smoke_voice.py  # StubASR → printed turn payload
```

### Turn body

`build_turn` emits `session_id`, `turn_id`, `speaker_role`, and `text`.
`created_at` is included only when passed. `speaker_role` is a free string
(`"user"` in the smoke script). `new_session_id()` is reused across turns;
`new_turn_id()` is unique per turn.

`TurnAudioAdapter.transcribe` accepts audio `bytes` or a filesystem path and
returns text. That text is the `text` field. The turn JSON has no audio key.

### Smoke test

```bash
python scripts/smoke_voice.py
```

Expects exit 0 offline. Prints the turn payload from `StubASR`, then a
text-first payload with `created_at` omitted. Brain is not required. If
`handle_turn_payload` imports, the script also posts the stub turn and prints
`speech_action`.


