# VeraLock demo UI

Single-page demo that sends text turns to fixture-mode Brain (`POST /v1/turn`) and shows the `TurnResult`: speech action, clarification question, commitment fields, and session transcript. After each turn, Brain's persist handoff writes Vault rows into a temporary SQLite file. When a turn has a `commitment_id`, the page loads provenance through `evidence.api.evidence_payload` (the same JSON as `GET /v1/evidence/{id}`).

No API keys. The demo entrypoint does not modify `intelligence/`.

## Run

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python frontend/demo_server.py
```

Open **http://127.0.0.1:8000**.

`Brain ok · fixture mode` in the header means `GET /health` succeeded. Startup prints the SQLite path. The default is a new file from `tempfile` (for example `/tmp/veralock-demo-….sqlite`). Stopping the server does not delete it; start again for an empty Vault, or pass `--db` to reuse a file.

```bash
python frontend/demo_server.py --host 127.0.0.1 --port 8000 --db /tmp/veralock-demo.sqlite
```

## What the page does

- Session id is generated automatically. **New session** starts a fresh transcript.
- Speaker role defaults to `user`.
- **Send turn** posts `session_id`, `turn_id`, `speaker_role`, `text`, `created_at`, `intervened`, and any active commitments from earlier turns in the session.
- The last response shows `speech_action` (`SILENT` or `CLARIFY`), `clarification_question` when Brain asks one, and commitment `status`, `canonical_text`, `topic_id`, `is_acknowledgement`, and `is_intention_only`.
- The transcript keeps every turn in the session so a multi-turn demo stays readable (stored in this browser).
- **Vault evidence** loads when `commitment_id` is present. If SQLite has no row yet, the panel says so and the turn result stays on screen.
- Filter skip and `policy_notes` are in the **Debug** disclosure on the last response. It opens automatically when the cost gate skips the turn.

## Sample prompts

These match the fixture reasoner (no network):

| Chip | Text | Fixture result |
| --- | --- | --- |
| Clear commitment | `I will send the proposal by Friday.` | `CONFIRMED`, speech `SILENT` |
| Acknowledgement | `Sounds good.` | `NO_COMMITMENT`, `is_acknowledgement` |
| Ambiguous | `I'll handle the budget review soon.` | `CLARIFY`, `AWAITING_CLARIFICATION` |
| Ambiguous follow-up | `Maybe the budget review sometime, I'm not sure.` | same session after the ambiguous chip → `UNRESOLVED_AMBIGUOUS`, speech `SILENT` |
| Intention only | `I might send the proposal by Friday.` | `NO_COMMITMENT`, `is_intention_only` |
| Filter skip | `um` | `skipped_by_filter`, reason `filler` |

Check **Intervened** and send a clear commitment to hold it off `CONFIRMED` (`blocked_auto_confirm`).

## Persist wiring

`frontend/demo_server.py` is the only composition point:

```python
import tempfile

from intelligence.api import create_app
from storage.db import connect, init_schema
from storage.persist_sink import SqlitePersistSink

db_path = tempfile.NamedTemporaryFile(
    prefix="veralock-demo-", suffix=".sqlite", delete=False
).name
conn = connect(db_path, check_same_thread=False)
init_schema(conn)
app = create_app(persist=SqlitePersistSink(conn))
```

`create_app` builds a fixture-mode pipeline. The default sink on Brain is `NullPersistPort`; this process replaces it with `SqlitePersistSink`. Brain emits the shared `PersistHandoff` (including `from_status`) and `POST /v1/turn` still returns only the `TurnResult` dict.

After a clear commitment, `GET /v1/demo/status` shows `db_path`. That file has a conversation row, a turn row, and a commitment row with status `CONFIRMED`.

## Demo routes

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Demo page |
| `GET` | `/health` | Brain health (`{"status":"ok","component":"brain"}`) |
| `POST` | `/v1/turn` | Brain turn (`TurnResult` JSON) |
| `GET` | `/v1/evidence/{commitment_id}` | Vault provenance (`evidence.api.evidence_payload`). 404 if the row is missing |
| `GET` | `/v1/sessions/{session_id}` | Stored turns and commitments for the session |
| `GET` | `/v1/demo/status` | Fixture flag and SQLite path |

`POST /v1/turn` body fields are the Brain contract: `session_id`, `turn_id`, `speaker_role`, `text`, and optional `intervened`, `created_at`, `active_commitments`, `conversation_id`, `intervention_reason`.
