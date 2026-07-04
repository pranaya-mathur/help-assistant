"""Inspect session database — Postgres (DATABASE_URL) or SQLite fallback.

Run from repo root:
  python scripts/inspect_db.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SQLITE_DB = ROOT / "data" / "sessions" / "chat_sessions.db"


def _print_sessions(rows: list[dict]) -> None:
    print("\n=== ALL SESSIONS (newest first) ===")
    if not rows:
        print("  (no sessions yet)")
        return
    for i, r in enumerate(rows, 1):
        lp = r.get("lead_profile") or {}
        meta = r.get("metadata") or {}
        hist = r.get("conversation_history") or []
        if meta.get("crm_dispatch_fingerprint"):
            crm = "sent"
        elif meta.get("crm_dispatch_pending_fingerprint"):
            crm = "pending"
        else:
            crm = "-"
        updated = str(r.get("updated_at", ""))[:19]
        ip = meta.get("first_client_ip") or meta.get("last_client_ip") or "-"
        print(
            f"{i}. {updated} | {r.get('intent') or '-'}/{r.get('stage') or '-'} | "
            f"msgs:{len(hist)} | {lp.get('name') or '-'} | "
            f"{lp.get('email') or '-'} | {lp.get('company') or '-'} | IP:{ip} | CRM:{crm}"
        )
        print(f"   id: {r['session_id']}")

    print("\n=== LATEST SESSION DETAIL ===")
    r = rows[0]
    lp = r.get("lead_profile") or {}
    meta = r.get("metadata") or {}
    hist = r.get("conversation_history") or []
    print("Session ID:", r["session_id"])
    print("Updated:", r.get("updated_at"))
    print("Intent/Stage:", r.get("intent"), "/", r.get("stage"))
    if meta.get("first_client_ip") or meta.get("last_client_ip"):
        print("Client IP:", meta.get("first_client_ip") or "-", "(first)", "|", meta.get("last_client_ip") or "-", "(last)")
    if meta.get("visitor_referrer"):
        print("Visitor referrer:", str(meta.get("visitor_referrer"))[:120])
    if meta.get("user_agent"):
        print("User-Agent:", str(meta.get("user_agent"))[:120])
    visitor_bits = []
    for key in ("visitor_timezone", "visitor_language", "visitor_scroll_depth_pct"):
        if meta.get(key) is not None:
            visitor_bits.append(f"{key}={meta.get(key)}")
    if visitor_bits:
        print("Visitor:", ", ".join(visitor_bits))
    print("\nLead profile:")
    print(json.dumps(lp, indent=2))
    print("\nMetadata:")
    print(json.dumps(meta, indent=2))
    print(f"\nConversation ({len(hist)} messages, last 4):")
    for m in hist[-4:]:
        role = str(m.get("role", "")).upper()
        text = str(m.get("content", "")).replace("\n", " ")
        if len(text) > 200:
            text = text[:200] + "..."
        print(f"  [{role}] {text}")


def _print_feedback(rows: list[dict]) -> None:
    print("\n=== FEEDBACK ===")
    if not rows:
        print("  (none yet)")
        return
    for row in rows:
        emoji = "👍" if row.get("rating") == 1 else "👎"
        created = str(row.get("created_at", ""))[:19]
        sid = str(row.get("session_id", ""))[:8]
        mid = str(row.get("message_id", ""))[:12]
        print(f"  {created} | {emoji} | session={sid}... | msg={mid}...")


def inspect_postgres() -> bool:
    from app.config.settings import get_settings

    settings = get_settings()
    if not settings.database_url:
        return False
    if settings.session_store_backend.strip().lower() != "postgres":
        print(f"SESSION_STORE_BACKEND={settings.session_store_backend!r} — use SQLite path")
        return False

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        tables = conn.execute(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name
            """
        ).fetchall()

        counts = {}
        for table in ("chat_sessions", "feedback", "analytics_events", "conversation_turns", "crm_dispatches"):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[table] = int(row["n"])

        session_rows = conn.execute(
            """
            SELECT session_id, updated_at, intent, stage,
                   lead_profile, conversation_history, metadata
            FROM chat_sessions
            ORDER BY updated_at DESC
            """
        ).fetchall()

        fb_rows = conn.execute(
            """
            SELECT session_id, message_id, rating, comment, created_at
            FROM feedback ORDER BY created_at DESC LIMIT 10
            """
        ).fetchall()

        event_rows = conn.execute(
            """
            SELECT event_type, session_id, client_ip, created_at
            FROM analytics_events ORDER BY created_at DESC LIMIT 10
            """
        ).fetchall()

    print("=== DATABASE (PostgreSQL) ===")
    print(f"URL: {settings.database_url.split('@')[-1] if '@' in settings.database_url else '(configured)'}")
    print(f"Tables: {[t['table_name'] for t in tables]}")
    print("\n=== COUNTS ===")
    for name, n in counts.items():
        print(f"{name}: {n}")

    sessions = [dict(r) for r in session_rows]
    _print_sessions(sessions)
    _print_feedback([dict(r) for r in fb_rows])

    print("\n=== RECENT ANALYTICS EVENTS ===")
    if not event_rows:
        print("  (none yet)")
    else:
        for row in event_rows:
            ip = row.get("client_ip") or "-"
            print(f"  {str(row['created_at'])[:19]} | {row['event_type']} | {row['session_id']} | IP:{ip}")

    return True


def inspect_sqlite() -> None:
    if not SQLITE_DB.exists():
        print(f"SQLite DB not found: {SQLITE_DB}")
        return

    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row

    print("=== DATABASE (SQLite) ===")
    print(f"File: {SQLITE_DB.resolve()}")
    print(f"Size: {SQLITE_DB.stat().st_size:,} bytes")

    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    print(f"Tables: {tables}")

    session_count = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
    fb_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    print("\n=== COUNTS ===")
    print(f"chat_sessions: {session_count}")
    print(f"feedback: {fb_count}")

    rows = conn.execute(
        """
        SELECT session_id, updated_at, intent, stage,
               conversation_history, lead_profile, metadata
        FROM chat_sessions ORDER BY updated_at DESC
        """
    ).fetchall()

    sessions = []
    for r in rows:
        sessions.append(
            {
                "session_id": r["session_id"],
                "updated_at": r["updated_at"],
                "intent": r["intent"],
                "stage": r["stage"],
                "lead_profile": json.loads(r["lead_profile"]),
                "conversation_history": json.loads(r["conversation_history"]),
                "metadata": json.loads(r["metadata"] or "{}"),
            }
        )
    _print_sessions(sessions)

    fb_rows = conn.execute(
        "SELECT session_id, message_id, rating, comment, created_at "
        "FROM feedback ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    _print_feedback([dict(r) for r in fb_rows])


def main() -> None:
    if not inspect_postgres():
        inspect_sqlite()


if __name__ == "__main__":
    main()
