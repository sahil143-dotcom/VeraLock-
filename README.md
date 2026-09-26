# VeraLock

Hackathon monorepo. Vault owns `storage/`, `evidence/`, and `followup/`.

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
