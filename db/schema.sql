-- MobCoder Sales Agent — PostgreSQL schema (reference)
-- Prefer versioned migrations: python scripts/migrate_db.py
-- Source of truth: db/migrations/*.sql

-- 001_core.sql + 002_analytics.sql combined for manual review / psql bootstrap.
-- Requires PostgreSQL 13+ (gen_random_uuid built-in).

\i db/migrations/001_core.sql
\i db/migrations/002_analytics.sql

-- Least-privilege roles: see db/roles.example.sql
-- TTL cleanup (schedule daily): python scripts/prune_sessions.py
