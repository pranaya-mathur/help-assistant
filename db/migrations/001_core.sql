-- Core session + feedback tables (required for SESSION_STORE_BACKEND=postgres)

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    lead_profile          JSONB NOT NULL DEFAULT '{}'::jsonb,
    conversation_history  JSONB NOT NULL DEFAULT '[]'::jsonb,
    intent                TEXT NOT NULL DEFAULT '',
    stage                 TEXT NOT NULL DEFAULT 'discover',
    metadata              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chat_sessions_stage_check CHECK (
        stage IN ('discover', 'educate', 'qualify', 'convert')
    )
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated_at
    ON chat_sessions (updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_intent
    ON chat_sessions (intent)
    WHERE intent <> '';

CREATE INDEX IF NOT EXISTS idx_chat_sessions_stage
    ON chat_sessions (stage);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_metadata_gin
    ON chat_sessions USING gin (metadata jsonb_path_ops);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_lead_email
    ON chat_sessions ((lead_profile ->> 'email'))
    WHERE (lead_profile ->> 'email') IS NOT NULL
      AND (lead_profile ->> 'email') <> '';

CREATE TABLE IF NOT EXISTS feedback (
    id           BIGSERIAL PRIMARY KEY,
    session_id   UUID NOT NULL REFERENCES chat_sessions (session_id) ON DELETE CASCADE,
    message_id   TEXT NOT NULL,
    rating       SMALLINT NOT NULL,
    comment      TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT feedback_rating_check CHECK (rating IN (-1, 1)),
    CONSTRAINT feedback_session_message_unique UNIQUE (session_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_created_at
    ON feedback (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_feedback_session_id
    ON feedback (session_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chat_sessions_updated_at ON chat_sessions;
CREATE TRIGGER trg_chat_sessions_updated_at
    BEFORE UPDATE ON chat_sessions
    FOR EACH ROW
    EXECUTE PROCEDURE set_updated_at();
