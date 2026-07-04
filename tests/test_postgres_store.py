from __future__ import annotations

import os

import pytest

from app.infra.postgres import (
    persist_analytics_event,
    record_crm_dispatch_queued,
    run_migrations,
    update_crm_dispatch_status,
)
from app.sessions.store import get_session_store, reset_session_store_for_tests


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "").strip()


pytestmark = pytest.mark.skipif(
    not _database_url(),
    reason="DATABASE_URL not set (Postgres integration tests skipped)",
)


@pytest.fixture
def postgres_store(monkeypatch):
    url = _database_url()
    monkeypatch.setenv("SESSION_STORE_BACKEND", "postgres")
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTO_MIGRATE_DB", "true")
    reset_session_store_for_tests()
    run_migrations(url)
    store = get_session_store()
    yield store
    reset_session_store_for_tests()


def test_postgres_session_store_persists_lead_profile(postgres_store):
    sid = postgres_store.create_session()
    postgres_store.save_turn(
        sid,
        "Need an AI agent",
        "We can help.",
        {"email": "cto@acme.com", "project_type": "ai_agent"},
        intent="sales",
        stage="qualify",
    )
    data = postgres_store.get(sid)
    assert data is not None
    assert data.lead_profile["email"] == "cto@acme.com"
    assert len(data.conversation_history) == 2


def test_postgres_get_or_create_honors_client_session_id(postgres_store):
    """D1 regression: get_or_create used to silently mint a brand-new UUID
    instead of the client-supplied one, fragmenting history/analytics across
    two different session rows for what the widget believed was one session."""
    import uuid

    client_id = str(uuid.uuid4())
    session = postgres_store.get_or_create(client_id)
    assert session.session_id == client_id

    # Second call with the same id must return the same row, not create another.
    again = postgres_store.get_or_create(client_id)
    assert again.session_id == client_id

    # A turn saved under the client id must actually persist there.
    postgres_store.save_turn(client_id, "hi", "hello", {"email": "a@b.com"})
    data = postgres_store.get(client_id)
    assert data is not None
    assert len(data.conversation_history) == 2


def test_postgres_save_turn_concurrent_writes_dont_lose_turns(postgres_store):
    """D3 regression: save_turn used to read history, append, and write across
    two separate steps with no lock — concurrent requests on the same session
    (double-send, retried request, two tabs) would read the same stale history
    and overwrite each other's turns. Verified empirically pre-fix: 20 concurrent
    writers on one session left only 2 of 40 expected messages. Now uses
    SELECT ... FOR UPDATE to serialize concurrent writers on the same row."""
    import threading

    sid = postgres_store.create_session()
    n = 20

    def worker(i: int) -> None:
        postgres_store.save_turn(sid, f"msg-{i}", f"reply-{i}", {}, intent="help", stage="discover")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    data = postgres_store.get(sid)
    assert data is not None
    assert len(data.conversation_history) == n * 2


def test_postgres_get_or_create_falls_back_on_invalid_id(postgres_store):
    session = postgres_store.get_or_create("not-a-real-uuid")
    assert session.session_id != "not-a-real-uuid"
    import uuid

    uuid.UUID(session.session_id)  # must still be a valid UUID


def test_postgres_session_metadata_urls(postgres_store):
    sid = postgres_store.create_session()
    postgres_store.update_metadata(
        sid,
        page_url="https://mobcoder.ai/contact",
        referrer="https://google.com",
    )
    data = postgres_store.get(sid)
    assert data.metadata["first_page_url"] == "https://mobcoder.ai/contact"
    assert data.metadata["referrer"] == "https://google.com"


def test_postgres_feedback_upsert(postgres_store):
    sid = postgres_store.create_session()
    postgres_store.save_feedback(sid, "msg-1", 1, "great")
    postgres_store.save_feedback(sid, "msg-1", -1, "changed")
    rows = postgres_store.list_feedback(session_id=sid, limit=10)
    assert len(rows) == 1
    assert rows[0]["rating"] == -1
    assert rows[0]["comment"] == "changed"


def test_postgres_analytics_and_crm_records(postgres_store, monkeypatch):
    monkeypatch.setenv("PERSIST_ANALYTICS_EVENTS", "true")
    sid = postgres_store.create_session()
    persist_analytics_event(
        {
            "event": "message_sent",
            "session_id": sid,
            "request_id": "req-1",
            "timestamp": "2026-06-15T12:00:00+00:00",
            "intent": "sales",
        }
    )
    record_crm_dispatch_queued(
        session_id=sid,
        fingerprint="abc123",
        request_id="req-1",
        intent="sales",
        lead_score="hot",
        lead_score_numeric=85,
        meeting_readiness="ready",
        payload={"conversation_summary": "CTO wants AI agent"},
    )
    update_crm_dispatch_status(
        session_id=sid,
        fingerprint="abc123",
        status="success",
    )

    from app.infra.postgres import get_pool

    pool = get_pool()
    with pool.connection() as conn:
        events = conn.execute(
            "SELECT COUNT(*) AS n FROM analytics_events WHERE session_id = %s::uuid",
            (sid,),
        ).fetchone()
        crm = conn.execute(
            "SELECT status FROM crm_dispatches WHERE session_id = %s::uuid AND fingerprint = %s",
            (sid, "abc123"),
        ).fetchone()
        turns = conn.execute(
            "SELECT COUNT(*) AS n FROM conversation_turns WHERE session_id = %s::uuid",
            (sid,),
        ).fetchone()

    assert int(events["n"]) >= 1
    assert crm["status"] == "success"
    assert int(turns["n"]) == 0
