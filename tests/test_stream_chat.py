import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


@patch("app.api.chat_service.get_settings")
@patch("app.api.chat_service.process_chat")
def test_stream_legacy_when_disabled(mock_chat, mock_settings, client):
    from app.api.schemas import ChatResponse, LeadProfileInput
    from app.config.settings import Settings

    mock_settings.return_value = Settings(enable_chat_streaming=False)
    mock_chat.return_value = ChatResponse(
        response="Hello",
        request_id="req-test",
        intent="help",
        page_category="general",
        citations=[],
        risk_flags=[],
        needs_contact_info=False,
        profile_question="",
        ready_for_booking=False,
        session_id="s1",
        lead_profile=LeadProfileInput(),
    )
    res = client.post(
        "/api/v1/chat/stream",
        json={"message": "What services?", "stream": True},
    )
    assert res.status_code == 200
    assert "Hello" in res.text
    mock_chat.assert_called_once()


@patch("app.api.chat_service.get_settings")
@patch("app.api.chat_service.run_agent_stream_events")
@patch("app.api.chat_service.get_session_store")
def test_stream_tokens_when_enabled(mock_store, mock_stream, mock_settings, client):
    from app.config.settings import Settings
    from app.sessions.store import SessionData

    mock_settings.return_value = Settings(enable_chat_streaming=True)
    mock_store.return_value.get_or_create.return_value = SessionData(
        session_id="s1",
        lead_profile={},
        conversation_history=[],
        intent="",
        stage="discover",
        updated_at="2020-01-01",
        metadata={},
    )

    async def fake_events(**kwargs):
        yield {"type": "token", "content": "Mob"}
        yield {"type": "token", "content": "Coder"}
        yield {
            "type": "done",
            "response": "MobCoder builds AI.",
            "state": {
                "final_response": "MobCoder builds AI.",
                "intent": "help",
                "page_category": "ai_agents",
                "stage": "educate",
                "lead_score": "cold",
                "citations": [],
                "risk_flags": [],
                "needs_contact_info": False,
                "profile_question": "",
                "ready_for_booking": False,
                "lead_profile": {},
                "grounding_passed": True,
                "grounding_rewritten": False,
                "suggested_replies": ["Book a discovery call"],
            },
        }

    mock_stream.side_effect = fake_events

    res = client.post(
        "/api/v1/chat/stream",
        json={"message": "What AI services?", "stream": True},
    )
    assert res.status_code == 200
    lines = [ln for ln in res.text.split("\n") if ln.startswith("data:")]
    events = []
    for ln in lines:
        raw = ln.replace("data:", "").strip()
        if raw == "[DONE]":
            continue
        events.append(json.loads(raw))
    types = [e.get("type") for e in events]
    assert "token" in types
    assert "done" in types
    assert "meta" in types
