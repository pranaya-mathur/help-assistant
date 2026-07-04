"""Print CRM dispatch rows and qualified sessions from Postgres.

Run from repo root:
  python scripts/inspect_crm.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from app.config.settings import get_settings

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL not set.")
        return

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        dispatches = conn.execute(
            """
            SELECT d.*, s.lead_profile, s.metadata AS session_metadata
            FROM crm_dispatches d
            JOIN chat_sessions s ON s.session_id = d.session_id
            ORDER BY d.created_at DESC
            """
        ).fetchall()

        events = conn.execute(
            """
            SELECT event_type, session_id, request_id, created_at, payload
            FROM analytics_events
            WHERE event_type IN (
                'lead_qualified', 'crm_dispatch_queued',
                'crm_dispatch_success', 'crm_dispatch_failed'
            )
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()

    print("=== CRM DISPATCHES (crm_dispatches table) ===")
    if not dispatches:
        print("  (none yet — lead must qualify with email + ready_for_booking)")
    for row in dispatches:
        lp = row.get("lead_profile") or {}
        print(f"\n{row['created_at']} | status={row['status']} | session={row['session_id']}")
        print(f"  intent={row['intent']} | lead_score={row['lead_score']} | readiness={row['meeting_readiness']}")
        print(f"  name={lp.get('name') or '-'} | email={lp.get('email') or '-'} | company={lp.get('company') or '-'}")
        if row.get("error_message"):
            print(f"  error: {row['error_message'][:200]}")
        payload = row.get("payload") or {}
        if payload.get("conversation_summary"):
            summary = str(payload["conversation_summary"]).replace("\n", " ")
            print(f"  summary: {summary[:180]}...")

    print("\n=== CRM ANALYTICS EVENTS ===")
    if not events:
        print("  (none)")
    for row in events:
        print(f"  {str(row['created_at'])[:19]} | {row['event_type']} | {row['session_id']}")


if __name__ == "__main__":
    main()
