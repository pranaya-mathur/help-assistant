"""Regression: the soft qualification ask must fire through the real hot path.

The original bug: default_state hardcodes stage="discover" and the persisted
session stage is never threaded back into run_agent, while
should_append_qualification only fires when stage == "qualify". The gate was
therefore permanently shut in live traffic even though node-level unit tests
(which pass stage="qualify" explicitly) were green. append_qualification now
recomputes the stage inline; these tests exercise the full graph the way
process_chat does.
"""

from unittest.mock import patch

_CHUNK = {
    "chunk_id": "c1",
    "page_title": "AI Services",
    "source_url": "https://mobcoder.ai/ai",
    "chunk_text": "MobCoder delivers agentic AI and LLM solutions.",
    "citation_text": "Source: AI",
    "score": 0.9,
    "rerank_score": 0.95,
}

# Two prior user turns — the MIN_SALES_TURNS_BEFORE_QUALIFY threshold.
_RAPPORT_HISTORY = [
    {"role": "user", "content": "What AI services do you offer?"},
    {"role": "assistant", "content": "MobCoder builds agentic AI systems."},
    {"role": "user", "content": "We are exploring a support chatbot."},
    {"role": "assistant", "content": "We can help with LLM-based support agents."},
]


def _run(monkeypatch, *, operating_mode: str, prior_intent: str):
    monkeypatch.setenv("OPERATING_MODE", operating_mode)
    # The repo .env carries the help-pilot value (false); sales mode ships
    # with qualification on, and pydantic-settings lets the process env win.
    monkeypatch.setenv("ENABLE_LEAD_QUALIFICATION", "true" if operating_mode != "help" else "false")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    from app.agent.graph import run_agent

    return run_agent(
        user_query="What would the architecture look like for that?",
        conversation_history=list(_RAPPORT_HISTORY),
        lead_profile={},
        prior_intent=prior_intent,
    )


@patch("app.agent.nodes._llm_text", return_value="MobCoder builds AI agents for enterprises.")
@patch("app.agent.nodes._llm_json", return_value={})
@patch("app.agent.nodes.get_retriever")
def test_sales_qualify_fires_on_hot_path(mock_retriever, mock_json, mock_llm, monkeypatch):
    """Full mode + rapport built + no contact info => the ask must appear."""
    mock_retriever.return_value.retrieve.return_value = [_CHUNK]
    state = _run(monkeypatch, operating_mode="full", prior_intent="sales")

    assert state.get("needs_contact_info") is True
    assert state.get("profile_question")
    assert state["profile_question"] in state.get("final_response", "")


@patch("app.agent.nodes._llm_text", return_value="MobCoder builds AI agents for enterprises.")
@patch("app.agent.nodes._llm_json", return_value={})
@patch("app.agent.nodes.get_retriever")
def test_help_mode_never_asks_on_hot_path(mock_retriever, mock_json, mock_llm, monkeypatch):
    """Pilot help mode keeps the hot path free of qualification asks."""
    mock_retriever.return_value.retrieve.return_value = [_CHUNK]
    state = _run(monkeypatch, operating_mode="help", prior_intent="help")

    assert not state.get("needs_contact_info")
    assert not state.get("profile_question")


@patch("app.agent.nodes._llm_text", return_value="MobCoder builds AI agents for enterprises.")
@patch("app.agent.nodes._llm_json", return_value={})
@patch("app.agent.nodes.get_retriever")
def test_no_ask_once_contact_is_captured(mock_retriever, mock_json, mock_llm, monkeypatch):
    """Known leads must not be re-interrogated (help-first UX)."""
    mock_retriever.return_value.retrieve.return_value = [_CHUNK]
    monkeypatch.setenv("OPERATING_MODE", "full")
    monkeypatch.setenv("ENABLE_LEAD_QUALIFICATION", "true")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    from app.agent.graph import run_agent

    state = run_agent(
        user_query="What would the architecture look like for that?",
        conversation_history=list(_RAPPORT_HISTORY),
        lead_profile={"name": "Priya Sharma", "email": "priya@finleap.io"},
        prior_intent="sales",
        lead_acknowledged=True,
    )
    assert not state.get("needs_contact_info")
    assert not state.get("profile_question")
