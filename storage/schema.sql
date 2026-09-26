-- Veralock Vault SQLite schema
-- Brain commitment fields mirrored exactly; Vault-only extras on commitments.
-- JSON columns: source_turn_ids, conditions. Every commitment and event stores source_turn_ids.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    session_id      TEXT,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    turn_id         TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id),
    speaker_role    TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turns_conversation ON turns(conversation_id);

CREATE TABLE IF NOT EXISTS commitments (
    -- Brain fields (never rename)
    commitment_id       TEXT PRIMARY KEY,
    topic_id            TEXT NOT NULL,
    status              TEXT NOT NULL,
    speaker_role        TEXT NOT NULL,
    canonical_text      TEXT NOT NULL,
    raw_span            TEXT,
    source_turn_ids     TEXT NOT NULL,  -- JSON list[str]
    clarification_count INTEGER NOT NULL DEFAULT 0,
    intervened          INTEGER NOT NULL DEFAULT 0,  -- bool
    is_acknowledgement  INTEGER NOT NULL DEFAULT 0,
    is_intention_only   INTEGER NOT NULL DEFAULT 0,
    confidence          REAL NOT NULL DEFAULT 0.0,
    conditions          TEXT,  -- JSON object | null
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    session_id          TEXT,

    -- Linkage (Vault)
    conversation_id     TEXT REFERENCES conversations(conversation_id),

    -- Vault-only extras
    condition                       TEXT,
    dependency_owner                TEXT,
    condition_status                TEXT,  -- PENDING|MET|FAILED|UNKNOWN
    due_at                          TEXT,
    next_followup_at                TEXT,
    superseded_by_commitment_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);
CREATE INDEX IF NOT EXISTS idx_commitments_next_followup ON commitments(next_followup_at);
CREATE INDEX IF NOT EXISTS idx_commitments_session ON commitments(session_id);

CREATE TABLE IF NOT EXISTS commitment_events (
    event_id        TEXT PRIMARY KEY,
    commitment_id   TEXT NOT NULL REFERENCES commitments(commitment_id),
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    source_turn_ids TEXT NOT NULL,  -- JSON list[str]; required on every transition
    note            TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_commitment_events_commitment ON commitment_events(commitment_id);

CREATE TABLE IF NOT EXISTS intervention_events (
    event_id        TEXT PRIMARY KEY,
    commitment_id   TEXT NOT NULL REFERENCES commitments(commitment_id),
    source_turn_ids TEXT NOT NULL,  -- JSON list[str]
    reason          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_intervention_events_commitment ON intervention_events(commitment_id);
