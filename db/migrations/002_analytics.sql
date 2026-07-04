-- Analytics, CRM audit, and normalized conversation turns (optional persistence)

CREATE TABLE IF NOT EXISTS analytics_events (
    id                BIGSERIAL PRIMARY KEY,
    session_id        UUID REFERENCES chat_sessions (session_id) ON DELETE SET NULL,
    event_type        TEXT NOT NULL,
    request_id        TEXT,
    page_url          TEXT,
    page_category     TEXT,
    intent            TEXT,
    project_type      TEXT,
    lead_score        TEXT,
    meeting_readiness TEXT,
    cta               TEXT,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_events_created_at
    ON analytics_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analytics_events_session_id
    ON analytics_events (session_id)
    WHERE session_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_analytics_events_type_created
    ON analytics_events (event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS crm_dispatches (
    id                 BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
    request_id         TEXT,
    fingerprint        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'queued',
    intent             TEXT NOT NULL DEFAULT '',
    lead_score         TEXT,
    lead_score_numeric INTEGER,
    meeting_readiness  TEXT,
    payload            JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message      TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at       TIMESTAMPTZ,

    CONSTRAINT crm_dispatches_status_check CHECK (
        status IN ('queued', 'success', 'failed')
    ),
    CONSTRAINT crm_dispatches_session_fingerprint_unique UNIQUE (session_id, fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_crm_dispatches_session_id
    ON crm_dispatches (session_id);

CREATE INDEX IF NOT EXISTS idx_crm_dispatches_status_created
    ON crm_dispatches (status, created_at DESC);

CREATE TABLE IF NOT EXISTS conversation_turns (
    id           BIGSERIAL PRIMARY KEY,
    session_id   UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
    turn_index   INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    request_id   TEXT,
    intent       TEXT,
    stage        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT conversation_turns_role_check CHECK (role IN ('user', 'assistant')),
    CONSTRAINT conversation_turns_session_turn_unique UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_id
    ON conversation_turns (session_id, turn_index);
