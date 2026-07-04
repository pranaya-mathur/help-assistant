from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.api.chat_service import sanitize_url_for_logs, state_to_response
from app.api.schemas import LeadProfileInput
from app.middleware.rate_limit import RateLimitMiddleware


def test_public_response_hides_internal_sales_metadata_by_default():
    resp = state_to_response(
        {
            "final_response": "Hello",
            "request_id": "req-1",
            "intent": "sales",
            "page_category": "services",
            "stage": "qualify",
            "lead_score": "hot",
            "citations": [],
            "risk_flags": [],
            "lead_profile": {},
        },
        "session-1",
    )

    assert resp.stage is None
    assert resp.lead_score is None


def test_debug_response_can_expose_internal_sales_metadata():
    resp = state_to_response(
        {
            "final_response": "Hello",
            "request_id": "req-1",
            "intent": "sales",
            "page_category": "services",
            "stage": "qualify",
            "lead_score": "hot",
            "citations": [],
            "risk_flags": [],
            "lead_profile": {},
        },
        "session-1",
        expose_internal_sales_metadata=True,
    )

    assert resp.stage == "qualify"
    assert resp.lead_score == "hot"


def _rate_limit_app(*, trust_proxy_headers: bool) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=1,
        trust_proxy_headers=trust_proxy_headers,
    )

    @app.post("/api/v1/chat")
    async def chat():
        return JSONResponse({"ok": True})

    return TestClient(app)


def test_rate_limiter_ignores_spoofed_forwarded_for_by_default():
    client = _rate_limit_app(trust_proxy_headers=False)

    assert client.post("/api/v1/chat", headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    blocked = client.post("/api/v1/chat", headers={"x-forwarded-for": "2.2.2.2"})
    assert blocked.status_code == 429
    assert blocked.headers.get("retry-after") == "60"
    assert blocked.headers.get("x-ratelimit-limit") == "1"
    assert blocked.headers.get("x-ratelimit-remaining") == "0"


def test_rate_limiter_can_trust_forwarded_for_when_enabled():
    client = _rate_limit_app(trust_proxy_headers=True)

    assert client.post("/api/v1/chat", headers={"x-forwarded-for": "1.1.1.1"}).status_code == 200
    assert client.post("/api/v1/chat", headers={"x-forwarded-for": "2.2.2.2"}).status_code == 200


def test_sanitize_url_for_logs_removes_query_and_fragment():
    assert (
        sanitize_url_for_logs("https://mobcoder.ai/services?email=a@example.com&utm_source=x#lead")
        == "https://mobcoder.ai/services"
    )


def test_hubspot_payload_includes_release_metadata(monkeypatch):
    from app.integrations import hubspot

    captured = {}

    class _Settings:
        hubspot_webhook_url = "https://example.test/hook"

    def fake_post(url, payload):
        captured["url"] = url
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
            "timeline": "Q3",
            "budget_band": "$50k",
        },
        intent="sales",
        session_id="s1",
        lead_score="hot",
        context={
            "request_id": "r1",
            "page_url": "https://mobcoder.ai/contact?utm_source=google",
            "first_page_url": "https://mobcoder.ai/",
            "last_page_url": "https://mobcoder.ai/contact?utm_source=google",
            "referrer": "https://google.com/",
            "utm_params": {"utm_source": "google"},
            "response_stage": "qualify",
            "lead_consent": True,
            "created_at": "2026-06-06T00:00:00+00:00",
            "conversation_summary": "CTO exploring AI agent.",
        },
    )

    payload = captured["payload"]
    assert payload["request_id"] == "r1"
    assert payload["page_url"] == "https://mobcoder.ai/contact?utm_source=google"
    assert payload["utm_params"] == {"utm_source": "google"}
    assert payload["lead_consent"] is True
    assert payload["response_stage"] == "qualify"
    assert payload["created_at"]
    assert "conversation_history" not in payload


@pytest.mark.asyncio
async def test_stream_retrieval_first_skips_retrieval_for_unsafe_query(monkeypatch):
    import app.agent.stream_runner as sr

    def fake_safety(state):
        return {**state, "is_safe": False, "final_response": "I can't help with that."}

    def fake_retrieve(state):
        raise AssertionError("retrieve_sources must not run for an unsafe query")

    monkeypatch.setattr(sr, "safety_check", fake_safety)
    monkeypatch.setattr(sr, "retrieve_sources", fake_retrieve)

    state = {
        "user_query": "something unsafe",
        "conversation_history": [],
        "lead_profile": {},
        "page_url": "",
        "prior_intent": "",
    }

    out = await sr._run_retrieval_first_async(state)

    assert out["is_safe"] is False
    assert out["retrieved_chunks"] == []
    assert out["current_page_chunks"] == []


@pytest.mark.asyncio
async def test_stream_retrieval_first_does_not_block_on_intent_llm(monkeypatch):
    """Hot path timings must not include classify_intent or update_lead_profile."""
    import app.agent.stream_runner as sr

    def fake_safety(state):
        return {**state, "is_safe": True}

    def fake_retrieve(state):
        return {**state, "retrieved_chunks": [{"chunk_id": "x"}], "current_page_chunks": []}

    monkeypatch.setattr(sr, "safety_check", fake_safety)
    monkeypatch.setattr(sr, "retrieve_sources", fake_retrieve)

    state = {
        "user_query": "Can you build a RAG chatbot?",
        "conversation_history": [],
        "lead_profile": {},
        "page_url": "",
        "prior_intent": "",
    }

    out = await sr._run_retrieval_first_async(state)
    timings = out.get("step_timings_ms") or {}

    assert out["retrieved_chunks"] == [{"chunk_id": "x"}]
    assert "retrieve_sources" in timings
    assert "classify_intent" not in timings
    assert "update_lead_profile" not in timings

