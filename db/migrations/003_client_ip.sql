-- Store client IP on analytics rows for reporting (optional join to sessions via session_id)

ALTER TABLE analytics_events
    ADD COLUMN IF NOT EXISTS client_ip TEXT;

CREATE INDEX IF NOT EXISTS idx_analytics_events_client_ip
    ON analytics_events (client_ip)
    WHERE client_ip IS NOT NULL;
