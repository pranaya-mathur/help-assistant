-- Local Postgres bootstrap for mobcoder-sales-agent
-- Run as a superuser (usually postgres):
--   psql -U postgres -f db/setup_local.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mobcoder') THEN
        CREATE ROLE mobcoder LOGIN PASSWORD 'mobcoder@123';
    ELSE
        ALTER ROLE mobcoder WITH PASSWORD 'mobcoder@123';
    END IF;
END
$$;

SELECT 'CREATE DATABASE mobcoder OWNER mobcoder'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mobcoder')\gexec

GRANT ALL PRIVILEGES ON DATABASE mobcoder TO mobcoder;
