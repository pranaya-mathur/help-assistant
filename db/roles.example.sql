-- Example least-privilege roles for managed Postgres (RDS, Cloud SQL, Supabase, etc.)
-- Run once as a superuser after migrations. Adjust passwords before use.

-- CREATE ROLE mobcoder_app LOGIN PASSWORD 'change-me';
-- CREATE ROLE mobcoder_readonly LOGIN PASSWORD 'change-me';

-- GRANT CONNECT ON DATABASE mobcoder TO mobcoder_app, mobcoder_readonly;
-- GRANT USAGE ON SCHEMA public TO mobcoder_app, mobcoder_readonly;

-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mobcoder_app;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mobcoder_app;

-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO mobcoder_readonly;

-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO mobcoder_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--     GRANT USAGE, SELECT ON SEQUENCES TO mobcoder_app;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--     GRANT SELECT ON TABLES TO mobcoder_readonly;
