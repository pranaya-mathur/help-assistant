from unittest.mock import patch

from app.agent.lead_extractor import (
    should_append_qualification,
    get_missing_lead_fields,
)
from app.agent.intent_classifier import classify_user_intent


def test_sales_intent_still_has_missing_fields():
    profile = {}
    missing = get_missing_lead_fields(profile, "sales")
    assert "project_need" in missing


def test_sales_no_qualify_on_first_turn():
    assert not should_append_qualification("sales", [], {})


def test_sales_qualify_after_two_prior_turns():
    history = [
        {"role": "user", "content": "What AI services do you offer?"},
        {"role": "assistant", "content": "MobCoder builds agentic AI systems."},
        {"role": "user", "content": "We need a customer support chatbot."},
        {"role": "assistant", "content": "We can help with LLM-based support agents."},
    ]
    assert should_append_qualification("sales", history, {}, stage="qualify")


def test_help_no_qualify_on_first_turn():
    assert not should_append_qualification("help", [], {})


def test_help_qualify_after_two_turns():
    history = [
        {"role": "user", "content": "What services?"},
        {"role": "assistant", "content": "We offer AI and mobile."},
        {"role": "user", "content": "Tell me more about AI"},
        {"role": "assistant", "content": "Agentic systems..."},
    ]
    assert should_append_qualification("help", history, {}, stage="qualify")


def test_discovery_call_is_help_not_booking():
    r = classify_user_intent(
        "Can you explain how you typically run discovery calls with new clients?"
    )
    assert r.intent in ("help", "general")
    assert r.intent != "booking"


def test_booking_phrase():
    r = classify_user_intent("I'd like to schedule a discovery call")
    assert r.intent == "booking"


@patch("app.agent.nodes._llm_text", return_value="MobCoder builds AI agents and mobile apps for enterprises.")
@patch("app.agent.nodes._llm_json", return_value={})
@patch("app.agent.nodes.get_retriever")
def test_sales_answers_before_qualify(mock_retriever, mock_json, mock_llm, monkeypatch):
    monkeypatch.setenv("OPERATING_MODE", "full")
    mock_retriever.return_value.retrieve.return_value = [
        {
            "chunk_id": "c1",
            "page_title": "AI Services",
            "source_url": "https://mobcoder.ai/ai",
            "chunk_text": "MobCoder delivers agentic AI and LLM solutions.",
            "citation_text": "Source: AI",
            "score": 0.9,
            "rerank_score": 0.95,
        }
    ]

    from app.config.settings import get_settings

    get_settings.cache_clear()
    from app.agent.graph import run_agent

    state = run_agent(
        user_query="We need an AI chatbot for customer support. Can MobCoder help?",
        conversation_history=[],
        lead_profile={},
    )

    response = state.get("final_response", "")
    assert len(response) > 40
    assert any(
        w in response.lower()
        for w in ("mobcoder", "ai", "agent", "chatbot", "support")
    )
    assert state.get("intent") == "sales"
    assert not state.get("needs_contact_info")
    assert "one quick question" not in response.lower()
