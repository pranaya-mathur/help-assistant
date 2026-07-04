from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()
_pool_conninfo: str | None = None


def verify_postgres_connectivity(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT 1")


def _ensure_schema_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     TEXT PRIMARY KEY,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def run_migrations(database_url: str) -> list[str]:
    """Apply pending *.sql files in db/migrations. Returns applied version names."""
    applied_versions: list[str] = []
    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.warning("No migration files found in %s", MIGRATIONS_DIR)
        return applied_versions

    with psycopg.connect(database_url, autocommit=False) as conn:
        _ensure_schema_migrations_table(conn)
        conn.commit()

        existing = {
            row[0]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        for path in migration_files:
            version = path.stem
            if version in existing:
                continue
            sql = path.read_text(encoding="utf-8")
            logger.info("Applying migration %s", version)
            with conn.transaction():
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
            applied_versions.append(version)

    return applied_versions


def init_pool(
    database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> ConnectionPool:
    global _pool, _pool_conninfo
    with _pool_lock:
        if _pool is not None and _pool_conninfo == database_url:
            return _pool
        if _pool is not None:
            _pool.close()
            _pool = None
        _pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        _pool_conninfo = database_url
        logger.info("Postgres connection pool ready (max=%s)", max_size)
        return _pool


def get_pool() -> ConnectionPool:
    if _pool is None:
        from app.config.settings import get_settings

        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        return init_pool(
            settings.database_url,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
        )
    return _pool


def reset_postgres_pool_for_tests() -> None:
    global _pool, _pool_conninfo
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _pool_conninfo = None


def prune_expired_sessions(ttl_days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
    pool = get_pool()
    with pool.connection() as conn:
        result = conn.execute(
            "DELETE FROM chat_sessions WHERE updated_at < %s",
            (cutoff,),
        )
        return result.rowcount or 0


def _parse_session_uuid(session_id: str | None) -> UUID | None:
    if not session_id:
        return None
    try:
        return UUID(str(session_id))
    except ValueError:
        return None


def persist_analytics_event(body: dict[str, Any]) -> None:
    from app.config.settings import get_settings

    settings = get_settings()
    if not settings.database_url or not settings.persist_analytics_events:
        return

    pool = get_pool()
    session_uuid = _parse_session_uuid(str(body.get("session_id") or ""))
    payload = {
        k: v
        for k, v in body.items()
        if k
        not in {
            "event",
            "timestamp",
            "session_id",
            "request_id",
            "page_url",
            "page_category",
            "intent",
            "project_type",
            "lead_score",
            "lead_score_label",
            "meeting_readiness",
            "cta",
            "client_ip",
        }
    }
    lead_score = body.get("lead_score") or body.get("lead_score_label")
    client_ip = (body.get("client_ip") or "")[:45] or None
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO analytics_events (
                session_id, event_type, request_id, page_url, page_category,
                intent, project_type, lead_score, meeting_readiness, cta,
                client_ip, payload, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, COALESCE(%s::timestamptz, now()))
            """,
            (
                session_uuid,
                body.get("event"),
                body.get("request_id"),
                body.get("page_url"),
                body.get("page_category"),
                body.get("intent"),
                body.get("project_type"),
                lead_score,
                body.get("meeting_readiness"),
                body.get("cta"),
                client_ip,
                json.dumps(payload, default=str),
                body.get("timestamp"),
            ),
        )


def record_crm_dispatch_queued(
    *,
    session_id: str,
    fingerprint: str,
    request_id: str = "",
    intent: str = "",
    lead_score: str = "",
    lead_score_numeric: int = 0,
    meeting_readiness: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    from app.config.settings import get_settings

    if not get_settings().database_url:
        return

    session_uuid = _parse_session_uuid(session_id)
    if session_uuid is None:
        return

    pool = get_pool()
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO crm_dispatches (
                session_id, request_id, fingerprint, status, intent,
                lead_score, lead_score_numeric, meeting_readiness, payload
            )
            VALUES (%s, %s, %s, 'queued', %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (session_id, fingerprint) DO UPDATE SET
                request_id = EXCLUDED.request_id,
                status = 'queued',
                intent = EXCLUDED.intent,
                lead_score = EXCLUDED.lead_score,
                lead_score_numeric = EXCLUDED.lead_score_numeric,
                meeting_readiness = EXCLUDED.meeting_readiness,
                payload = EXCLUDED.payload,
                error_message = NULL,
                completed_at = NULL,
                created_at = now()
            """,
            (
                session_uuid,
                request_id,
                fingerprint,
                intent,
                lead_score,
                lead_score_numeric,
                meeting_readiness,
                json.dumps(payload or {}, default=str),
            ),
        )


def update_crm_dispatch_status(
    *,
    session_id: str,
    fingerprint: str,
    status: str,
    error_message: str = "",
) -> None:
    from app.config.settings import get_settings

    if not get_settings().database_url:
        return

    session_uuid = _parse_session_uuid(session_id)
    if session_uuid is None:
        return

    pool = get_pool()
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE crm_dispatches
            SET status = %s,
                error_message = %s,
                completed_at = now()
            WHERE session_id = %s AND fingerprint = %s
            """,
            (status, error_message[:2000] if error_message else None, session_uuid, fingerprint),
        )
