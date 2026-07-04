from __future__ import annotations

import pytest

from app.api.attribution import persist_client_ip, persist_user_agent, persist_visitor_meta
from app.api.schemas import VisitorMeta
from app.sessions.store import SQLiteSessionStore


@pytest.fixture
def sqlite_store(tmp_path):
    return SQLiteSessionStore(str(tmp_path / "sessions.db"))


def test_persist_client_ip_first_and_last(sqlite_store):
    sid = sqlite_store.create_session()
    persist_client_ip(sqlite_store, sid, "192.168.1.10")
    data = sqlite_store.get(sid)
    assert data.metadata["first_client_ip"] == "192.168.1.10"
    assert data.metadata["last_client_ip"] == "192.168.1.10"

    persist_client_ip(sqlite_store, sid, "10.0.0.5")
    data = sqlite_store.get(sid)
    assert data.metadata["first_client_ip"] == "192.168.1.10"
    assert data.metadata["last_client_ip"] == "10.0.0.5"


def test_persist_user_agent_once(sqlite_store):
    sid = sqlite_store.create_session()
    persist_user_agent(sqlite_store, sid, "Mozilla/5.0 Test")
    persist_user_agent(sqlite_store, sid, "Other Agent")
    data = sqlite_store.get(sid)
    assert data.metadata["user_agent"] == "Mozilla/5.0 Test"


def test_persist_visitor_meta_captures_fingerprint(sqlite_store):
    sid = sqlite_store.create_session()
    meta = VisitorMeta(
        timezone="America/New_York",
        language="en-US",
        scroll_depth_pct=42,
        utm_source="google",
        referrer="https://google.com/",
    )
    persist_visitor_meta(sqlite_store, sid, meta)
    data = sqlite_store.get(sid)
    assert data.metadata["visitor_meta_captured"] is True
    assert data.metadata["visitor_timezone"] == "America/New_York"
    assert data.metadata["visitor_language"] == "en-US"
    assert data.metadata["visitor_scroll_depth_pct"] == 42
    assert data.metadata["utm_params"]["utm_source"] == "google"
    assert data.metadata["visitor_referrer"] == "https://google.com/"


def test_persist_visitor_referrer_when_referrer_already_set(sqlite_store):
    sid = sqlite_store.create_session()
    sqlite_store.patch_metadata(sid, {"referrer": "https://existing.com/"})
    persist_visitor_meta(
        sqlite_store,
        sid,
        VisitorMeta(timezone="UTC", language="en", referrer="https://google.com/"),
    )
    data = sqlite_store.get(sid)
    assert data.metadata["referrer"] == "https://existing.com/"
    assert data.metadata["visitor_referrer"] == "https://google.com/"
