from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock

import pytest

from app.agent.conversation_summary import build_conversation_summary
from app.agent.lead_intelligence import (
    classify_project_type,
    compute_lead_scoring,
    enrich_profile_from_text,
    extract_industry,
    extract_role,
    infer_decision_maker,
)
from app.agent.lead_extractor import compute_lead_score, extract_from_message
from app.middleware.rate_limit import MemoryRateLimiterBackend, RedisRateLimiterBackend
from app.observability.events import emit_event
from app.sessions.store import SQLiteSessionStore, get_session_store, reset_session_store_for_tests


class FakeRedis:
    """Minimal Redis fake for session store and rate limiter tests."""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._sorted: dict[str, dict[str, float]] = {}
        self._expiries: dict[str, int] = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            return self._strings.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        with self._lock:
            self._strings[key] = value
            if ex is not None:
                self._expiries[key] = ex

    def expire(self, key: str, seconds: int):
        with self._lock:
            self._expiries[key] = seconds

    def delete(self, key: str):
        with self._lock:
            self._strings.pop(key, None)
            self._sorted.pop(key, None)
            self._expiries.pop(key, None)

    def zrem(self, key: str, member: str):
        with self._lock:
            self._sorted.get(key, {}).pop(member, None)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key: str, start: float, end: float):
        self._ops.append(("zremrangebyscore", key, start, end))
        return self

    def zadd(self, key: str, mapping: dict[str, float]):
        self._ops.append(("zadd", key, mapping))
        return self

    def zcard(self, key: str):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key: str, seconds: int):
        self._ops.append(("expire", key, seconds))
        return self

    def zrem(self, key: str, member: str):
        self._ops.append(("zrem", key, member))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "zremrangebyscore":
                _, key, start, end = op
                bucket = self._redis._sorted.setdefault(key, {})
                to_drop = [m for m, score in bucket.items() if start <= score <= end]
                for member in to_drop:
                    bucket.pop(member, None)
                results.append(None)
            elif op[0] == "zadd":
                _, key, mapping = op
                bucket = self._redis._sorted.setdefault(key, {})
                bucket.update(mapping)
                results.append(None)
            elif op[0] == "zcard":
                _, key = op
                results.append(len(self._redis._sorted.get(key, {})))
            elif op[0] == "expire":
                _, key, seconds = op
                self._redis._expiries[key] = seconds
                results.append(True)
            elif op[0] == "zrem":
                _, key, member = op
                self._redis._sorted.get(key, {}).pop(member, None)
                results.append(None)
        self._ops.clear()
        return results


@pytest.fixture
def sqlite_store(tmp_path):
    db = tmp_path / "sessions.db"
    return SQLiteSessionStore(str(db))


def test_sqlite_session_store_persists_lead_profile(sqlite_store):
    sid = sqlite_store.create_session()
    sqlite_store.save_turn(
        sid,
        "Need an AI agent",
        "We can help.",
        {"email": "cto@acme.com", "project_type": "ai_agent"},
        intent="sales",
        stage="qualify",
    )
    data = sqlite_store.get(sid)
    assert data is not None
    assert data.lead_profile["email"] == "cto@acme.com"
    assert data.lead_profile["project_type"] == "ai_agent"
    assert len(data.conversation_history) == 2


def test_sqlite_session_metadata_urls(sqlite_store):
    sid = sqlite_store.create_session()
    sqlite_store.update_metadata(sid, page_url="https://mobcoder.ai/contact", referrer="https://google.com")
    data = sqlite_store.get(sid)
    assert data.metadata["first_page_url"] == "https://mobcoder.ai/contact"
    assert data.metadata["last_page_url"] == "https://mobcoder.ai/contact"


def test_redis_backend_requires_url(monkeypatch):
    monkeypatch.setenv("SESSION_STORE_BACKEND", "redis")
    reset_session_store_for_tests()
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        get_session_store()


def test_postgres_backend_requires_url(monkeypatch):
    monkeypatch.setenv("SESSION_STORE_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_session_store_for_tests()
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        get_session_store()


def test_redis_session_store_persistence(monkeypatch):
    fake = FakeRedis()

    def fake_client(_url):
        return fake

    monkeypatch.setenv("SESSION_STORE_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://fake/0")
    reset_session_store_for_tests()
    monkeypatch.setattr("app.infra.redis_client.get_redis_client", fake_client)

    store = get_session_store()
    sid = store.create_session()
    store.save_turn(
        sid,
        "hello",
        "hi",
        {"name": "Jane"},
        intent="sales",
        stage="discover",
    )
    reset_session_store_for_tests()
    monkeypatch.setattr("app.infra.redis_client.get_redis_client", fake_client)
    store2 = get_session_store()
    loaded = store2.get(sid)
    assert loaded is not None
    assert loaded.lead_profile["name"] == "Jane"
    assert fake._expiries[f"mobcoder:session:{sid}"] > 0


def test_memory_rate_limiter_blocks_after_limit():
    backend = MemoryRateLimiterBackend()
    assert backend.allow("1.2.3.4", 2) is True
    assert backend.allow("1.2.3.4", 2) is True
    assert backend.allow("1.2.3.4", 2) is False


def test_redis_rate_limiter_uses_fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("app.infra.redis_client.get_redis_client", lambda _url: fake)
    backend = RedisRateLimiterBackend("redis://fake/0")
    assert backend.allow("9.9.9.9", 1) is True
    assert backend.allow("9.9.9.9", 1) is False


def test_redis_rate_limiter_missing_url():
    with pytest.raises(RuntimeError, match="REDIS_URL"):
        RedisRateLimiterBackend(None)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("We need a LangGraph AI agent workflow", "ai_agent"),
        ("Build a RAG chatbot over internal docs", "rag_chatbot"),
        ("React Native iOS Android mobile app", "mobile_app"),
        ("Next.js SaaS dashboard web portal", "web_app"),
        ("Enterprise ERP internal platform", "enterprise_software"),
        ("HIPAA healthcare compliance platform", "healthcare_fintech"),
        ("Need staff augmentation dedicated team", "staff_augmentation"),
        ("Maintain and support our existing app", "maintenance_support"),
        ("AWS GCP DevOps cloud deployment", "cloud_devops"),
        ("Shopify ecommerce marketplace", "ecommerce"),
    ],
)
def test_project_type_classification(message, expected):
    assert classify_project_type(message) == expected


def test_role_and_industry_extraction():
    text = "I'm the CTO of a healthcare startup"
    assert extract_role(text) == "cto"
    assert extract_industry(text) == "healthcare"
    assert infer_decision_maker("cto", text) is True


def test_extract_from_message_enriches_profile():
    found = extract_from_message("I'm the CEO at a fintech firm, email ceo@bank.com")
    assert found["email"] == "ceo@bank.com"
    assert found["role"] == "ceo"
    assert found["industry"] == "fintech"
    assert found["decision_maker"] is True


def test_hot_lead_scores_higher_than_vague_lead():
    hot_profile = {
        "name": "Alex",
        "email": "alex@enterprise.com",
        "company": "BigCo",
        "project_need": "AI agent for sales automation",
        "project_type": "ai_agent",
        "timeline": "launch in 6 weeks",
        "budget_band": "$150k",
        "role": "cto",
        "industry": "fintech",
        "decision_maker": True,
    }
    cold_profile = {"project_need": "just exploring"}
    hot = compute_lead_scoring(
        hot_profile,
        "sales",
        user_query="Need to launch in 6 weeks, budget $150k",
        page_url="https://mobcoder.ai/contact",
    )
    cold = compute_lead_scoring(cold_profile, "sales", user_query="hi")
    assert hot["lead_score_numeric"] > cold["lead_score_numeric"]
    assert hot["lead_score"] == "hot"
    assert hot["meeting_readiness"] in {"ready", "booking_requested"}
    assert cold["lead_score"] == "cold"


def test_compute_lead_score_backward_compatible():
    profile = {"name": "Jane", "email": "jane@acme.com"}
    assert compute_lead_score(profile, "help") in {"warm", "cold", "hot"}


def test_conversation_summary_deterministic():
    summary = build_conversation_summary(
        lead={
            "project_type": "ai_agent",
            "project_need": "Sales AI agent",
            "timeline": "8 weeks",
            "budget_band": "$80k",
            "industry": "saas",
            "role": "cto",
            "company": "Acme",
        },
        history=[{"role": "user", "content": "Do you build LangGraph agents?"}],
        intent="sales",
        user_query="My email is cto@acme.com",
        page_url="https://mobcoder.ai/services",
    )
    assert "ai agent" in summary.lower()
    assert "Sales AI agent" in summary
    assert "8 weeks" in summary
    assert len(summary) >= 50


def test_hubspot_async_dispatch_does_not_block(monkeypatch):
    from app.integrations import hubspot

    class _Settings:
        hubspot_webhook_url = "https://example.test/hook"

    started = threading.Event()
    release = threading.Event()

    def slow_notify(*args, **kwargs):
        started.set()
        release.wait(timeout=5)
        return False

    monkeypatch.setattr(hubspot, "get_settings", lambda: _Settings())
    monkeypatch.setattr(hubspot, "notify_qualified_lead", slow_notify)

    t0 = time.perf_counter()
    hubspot.dispatch_qualified_lead_async(
        lead_profile={"email": "a@b.com"},
        intent="sales",
        session_id="s1",
        lead_score="hot",
        context={"request_id": "r1"},
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5
    assert started.wait(timeout=2)
    release.set()


def test_hubspot_payload_includes_intelligence_fields(monkeypatch):
    from app.integrations import hubspot

    captured = {}

    class _Settings:
        hubspot_webhook_url = "https://example.test/hook"

    def fake_post(url, payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(hubspot, "get_settings", lambda: _Settings())
    monkeypatch.setattr(hubspot, "_post_once", fake_post)

    assert hubspot.notify_qualified_lead(
        lead_profile={
            "name": "Jane",
            "email": "jane@example.com",
            "company": "Acme",
            "project_need": "AI agent",
            "project_type": "ai_agent",
            "timeline": "Q3",
            "budget_band": "$50k",
            "role": "cto",
            "industry": "healthcare",
            "decision_maker": True,
        },
        intent="sales",
        session_id="s1",
        lead_score="hot",
        context={
            "request_id": "r1",
            "conversation_summary": "CTO exploring AI agent for healthcare.",
            "lead_score_numeric": 82,
            "meeting_readiness": "ready",
        },
    )

    payload = captured["payload"]
    assert payload["conversation_summary"]
    assert payload["project_type"] == "ai_agent"
    assert payload["role"] == "cto"
    assert payload["industry"] == "healthcare"
    assert payload["lead_score_numeric"] == 82
    assert payload["meeting_readiness"] == "ready"
    assert payload["lead"]["project_type"] == "ai_agent"
    assert "conversation_history" not in payload


def test_analytics_event_payload_excludes_pii(caplog, monkeypatch):
    import logging

    monkeypatch.setenv("ANALYTICS_WEBHOOK_URL", "")
    with caplog.at_level(logging.INFO):
        emit_event(
            "lead_qualified",
            {
                "session_id": "sess-1",
                "page_url": "https://mobcoder.ai/services?utm_source=x",
                "intent": "sales",
                "lead_score_label": "hot",
                "project_type": "ai_agent",
                "meeting_readiness": "ready",
            },
        )
    records = [r.message for r in caplog.records if "analytics" in r.message]
    assert records
    assert "@" not in records[-1]


class _SalesModeSettings:
    """Stub settings representing sales/full mode with lead qualification on."""

    def is_help_mode(self) -> bool:
        return False

    enable_lead_qualification = True


def test_emit_post_chat_analytics_async_crm(monkeypatch):
    from app.api import chat_service
    from app.api.schemas import ChatRequest

    queued = {"called": False}

    def fake_dispatch(**kwargs):
        queued["called"] = True
        queued["summary"] = kwargs["context"]["conversation_summary"]

    monkeypatch.setattr(chat_service, "get_settings", lambda: _SalesModeSettings())
    monkeypatch.setattr(chat_service, "dispatch_qualified_lead_async", fake_dispatch)
    monkeypatch.setattr(chat_service, "message_sent", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "lead_qualified", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "help_answered", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "sales_answered", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "qualify_shown", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "booking_intent_detected", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "answer_not_found", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "grounding_failure", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "injection_blocked", lambda *a, **k: None)

    chat_service._emit_post_chat_analytics(
        session_id="s1",
        request=ChatRequest(
            message="Book a call", page_url="https://mobcoder.ai/contact", lead_consent=True
        ),
        state={
            "ready_for_booking": True,
            "intent": "sales",
            "lead_score": "hot",
            "lead_score_numeric": 75,
            "meeting_readiness": "ready",
            "final_response": "Thanks!",
            "lead_profile": {
                "name": "Jane",
                "email": "jane@acme.com",
                "project_need": "AI agent",
                "project_type": "ai_agent",
            },
            "stage": "convert",
            "page_category": "contact",
            "grounding_passed": True,
            "retrieved_chunks": [{}],
        },
        request_id="req-1",
        session_metadata={},
        conversation_history=[{"role": "user", "content": "Need AI agent"}],
    )
    assert queued["called"] is True
    assert "AI agent" in queued["summary"]


def test_emit_post_chat_analytics_skips_crm_without_consent(monkeypatch):
    from app.api import chat_service
    from app.api.schemas import ChatRequest

    queued = {"called": False}

    def fake_dispatch(**kwargs):
        queued["called"] = True

    monkeypatch.setattr(chat_service, "dispatch_qualified_lead_async", fake_dispatch)
    monkeypatch.setattr(chat_service, "message_sent", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "lead_qualified", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "help_answered", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "sales_answered", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "qualify_shown", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "booking_intent_detected", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "answer_not_found", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "grounding_failure", lambda *a, **k: None)
    monkeypatch.setattr(chat_service, "injection_blocked", lambda *a, **k: None)

    chat_service._emit_post_chat_analytics(
        session_id="s1",
        request=ChatRequest(
            message="Book a call", page_url="https://mobcoder.ai/contact", lead_consent=False
        ),
        state={
            "ready_for_booking": True,
            "intent": "sales",
            "lead_score": "hot",
            "lead_score_numeric": 75,
            "meeting_readiness": "ready",
            "final_response": "Thanks!",
            "lead_profile": {
                "name": "Jane",
                "email": "jane@acme.com",
                "project_need": "AI agent",
                "project_type": "ai_agent",
            },
            "stage": "convert",
            "page_category": "contact",
            "grounding_passed": True,
            "retrieved_chunks": [{}],
        },
        request_id="req-1",
        session_metadata={},
        conversation_history=[{"role": "user", "content": "Need AI agent"}],
    )
    assert queued["called"] is False


def test_should_dispatch_crm_ready_for_booking(monkeypatch):
    from app.api import chat_service
    from app.api.chat_service import _should_dispatch_crm

    monkeypatch.setattr(chat_service, "get_settings", lambda: _SalesModeSettings())
    assert _should_dispatch_crm({"ready_for_booking": True, "lead_profile": {}}) is True


def test_should_dispatch_crm_meeting_readiness_with_context(monkeypatch):
    from app.api import chat_service
    from app.api.chat_service import _should_dispatch_crm

    monkeypatch.setattr(chat_service, "get_settings", lambda: _SalesModeSettings())
    assert _should_dispatch_crm(
        {
            "ready_for_booking": False,
            "meeting_readiness": "ready",
            "intent": "sales",
            "lead_profile": {
                "email": "cto@acme.com",
                "project_need": "AI agent",
                "project_type": "ai_agent",
            },
        }
    )


def test_should_dispatch_crm_rejects_incomplete(monkeypatch):
    from app.api import chat_service
    from app.api.chat_service import _should_dispatch_crm

    monkeypatch.setattr(chat_service, "get_settings", lambda: _SalesModeSettings())
    assert not _should_dispatch_crm(
        {
            "ready_for_booking": False,
            "meeting_readiness": "ready",
            "lead_profile": {"email": "a@b.com"},
        }
    )


def test_should_dispatch_crm_false_in_help_mode():
    """Phase 1 pilot: help mode must never queue a CRM/HubSpot dispatch."""
    from app.api.chat_service import _should_dispatch_crm

    assert _should_dispatch_crm(
        {
            "ready_for_booking": True,
            "meeting_readiness": "booking_requested",
            "intent": "booking",
            "lead_profile": {
                "email": "cto@acme.com",
                "name": "Jane",
                "project_need": "AI agent",
            },
        }
    ) is False


def test_apollo_maps_title_to_role():
    from app.integrations.apollo import _map_person_to_profile

    out = _map_person_to_profile(
        {"title": "Chief Technology Officer", "organization": {"name": "Acme", "industry": "healthcare"}},
        {"email": "cto@acme.com"},
    )
    assert out["role"] == "cto"
    assert out["industry"] == "healthcare"
    assert out["decision_maker"] is True


def test_apollo_people_match_uses_post_and_api_key_header():
    from unittest.mock import MagicMock, patch

    from app.integrations.apollo import fetch_apollo_person_sync

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "person": {"title": "VP Engineering", "organization": {"name": "Acme Corp"}},
    }
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp

    with patch("app.integrations.apollo.httpx.Client", return_value=mock_client):
        with patch("app.integrations.apollo.get_settings") as mock_settings:
            mock_settings.return_value.apollo_api_key = "test-apollo-key"
            person = fetch_apollo_person_sync({"email": "jane@acme.com", "company": "Acme Corp"})

    assert person["title"] == "VP Engineering"
    mock_client.post.assert_called_once()
    call = mock_client.post.call_args
    assert call.args[0] == "https://api.apollo.io/api/v1/people/match"
    assert call.kwargs["headers"]["X-Api-Key"] == "test-apollo-key"
    assert call.kwargs["json"] == {"email": "jane@acme.com", "organization_name": "Acme Corp"}


def test_crm_dispatch_skipped_when_fingerprint_matches():
    from app.api.chat_service import _crm_already_dispatched, _crm_dispatch_fingerprint

    lead = {"email": "jane@acme.com", "project_need": "AI agent", "project_type": "ai_agent"}
    fp = _crm_dispatch_fingerprint(lead, "sales")
    assert _crm_already_dispatched({"crm_dispatch_fingerprint": fp}, fp)
    assert _crm_already_dispatched({"crm_dispatch_pending_fingerprint": fp}, fp)
    assert not _crm_already_dispatched({}, fp)


def test_crm_dispatch_retries_after_failure_clears_pending(sqlite_store):
    from app.api.chat_service import (
        _clear_crm_dispatch_pending,
        _crm_dispatch_fingerprint,
        _mark_crm_dispatch_pending,
    )

    sid = sqlite_store.create_session()
    lead = {"email": "jane@acme.com", "project_need": "RAG bot"}
    fp = _crm_dispatch_fingerprint(lead, "sales")
    _mark_crm_dispatch_pending(sid, fp)
    data = sqlite_store.get(sid)
    assert data.metadata.get("crm_dispatch_pending_fingerprint") == fp

    _clear_crm_dispatch_pending(sid, fp)
    data = sqlite_store.get(sid)
    assert not data.metadata.get("crm_dispatch_pending_fingerprint")


def test_emit_post_chat_analytics_skips_duplicate_crm(monkeypatch):
    from app.api import chat_service
    from app.api.schemas import ChatRequest

    dispatch_calls = {"count": 0}
    qualified_calls = {"count": 0}

    def fake_dispatch(**kwargs):
        dispatch_calls["count"] += 1

    monkeypatch.setattr(chat_service, "dispatch_qualified_lead_async", fake_dispatch)
    monkeypatch.setattr(
        chat_service,
        "lead_qualified",
        lambda *a, **k: qualified_calls.__setitem__("count", qualified_calls["count"] + 1),
    )
    for fn in (
        "message_sent",
        "help_answered",
        "sales_answered",
        "qualify_shown",
        "booking_intent_detected",
        "answer_not_found",
        "grounding_failure",
        "injection_blocked",
    ):
        monkeypatch.setattr(chat_service, fn, lambda *a, **k: None)

    state = {
        "ready_for_booking": True,
        "intent": "sales",
        "lead_score": "hot",
        "lead_score_numeric": 75,
        "meeting_readiness": "ready",
        "final_response": "Thanks!",
        "lead_profile": {"email": "jane@acme.com", "project_need": "AI agent"},
        "stage": "convert",
        "page_category": "contact",
        "grounding_passed": True,
        "retrieved_chunks": [{}],
    }
    metadata = {"crm_dispatch_fingerprint": chat_service._crm_dispatch_fingerprint(
        state["lead_profile"], "sales"
    )}

    chat_service._emit_post_chat_analytics(
        session_id="s-dup",
        request=ChatRequest(message="Follow up question", lead_consent=True),
        state=state,
        request_id="req-dup",
        session_metadata=metadata,
        conversation_history=[],
    )
    assert dispatch_calls["count"] == 0
    assert qualified_calls["count"] == 0


def test_chat_route_ignores_spoofed_client_ip(monkeypatch):
    """A caller-supplied client_ip in the request body must never survive into
    process_chat — it has to be overwritten with the server-derived IP, or
    Apollo enrichment / attribution becomes spoofable."""
    from fastapi.testclient import TestClient

    from app.api import chat_service
    from main import app

    captured: dict[str, str | None] = {}

    def fake_process_chat(request, **kwargs):
        captured["client_ip"] = request.client_ip
        return chat_service.ChatResponse(
            response="ok",
            request_id="req-1",
            intent="general",
            page_category="general",
            citations=[],
            risk_flags=[],
            needs_contact_info=False,
            profile_question="",
            ready_for_booking=False,
            session_id="s1",
            lead_profile=chat_service.LeadProfileInput(),
        )

    monkeypatch.setattr(chat_service, "process_chat", fake_process_chat)
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "process_chat", fake_process_chat)

    client = TestClient(app)
    res = client.post(
        "/api/v1/chat",
        json={"message": "hi", "client_ip": "6.6.6.6"},
    )
    assert res.status_code == 200
    assert captured["client_ip"] != "6.6.6.6"
