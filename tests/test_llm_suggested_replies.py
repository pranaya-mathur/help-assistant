import json

from app.agent.llm_suggested_replies import generate_llm_suggested_replies
from app.agent.suggested_replies import MAX_SUGGESTIONS, merge_suggested_replies


def test_merge_suggested_replies_dedupes_and_caps():
    heuristic = ["Book a discovery call", "Tell me about pricing"]
    llm = ["book a discovery call", "What about mobile apps?", "Extra chip"]
    merged = merge_suggested_replies(heuristic, llm, max_count=MAX_SUGGESTIONS)
    assert len(merged) <= MAX_SUGGESTIONS
    assert merged[0] == "Book a discovery call"
    assert merged.count("Book a discovery call") == 1
    assert "What about mobile apps?" in merged


def test_generate_llm_suggested_replies_disabled(monkeypatch):
    from app.config import settings as settings_mod

    monkeypatch.setenv("LLM_SUGGESTED_REPLIES_ENABLED", "false")
    settings_mod.get_settings.cache_clear()

    def fake_llm(_prompt: str) -> dict:
        return {"chips": ["Should not appear"]}

    chips = generate_llm_suggested_replies(
        user_query="hi",
        answer="We build AI agents.",
        page_category="ai_agents",
        page_title="AI",
        intent="help",
        stage="discover",
        llm_json_fn=fake_llm,
    )
    assert chips == []
    settings_mod.get_settings.cache_clear()


def test_generate_llm_suggested_replies_skips_out_of_scope(monkeypatch):
    from app.config import settings as settings_mod

    monkeypatch.setenv("LLM_SUGGESTED_REPLIES_ENABLED", "true")
    settings_mod.get_settings.cache_clear()

    called = {"n": 0}

    def fake_llm(_prompt: str) -> dict:
        called["n"] += 1
        return {"chips": ["Chip"]}

    chips = generate_llm_suggested_replies(
        user_query="weather",
        answer="I cannot help.",
        page_category="general",
        page_title="",
        intent="out_of_scope",
        stage="discover",
        llm_json_fn=fake_llm,
    )
    assert chips == []
    assert called["n"] == 0
    settings_mod.get_settings.cache_clear()


def test_generate_llm_suggested_replies_bad_json_fallback(monkeypatch):
    from app.config import settings as settings_mod

    monkeypatch.setenv("LLM_SUGGESTED_REPLIES_ENABLED", "true")
    settings_mod.get_settings.cache_clear()

    def bad_llm(_prompt: str) -> dict:
        raise json.JSONDecodeError("bad", "", 0)

    chips = generate_llm_suggested_replies(
        user_query="services?",
        answer="We offer custom software and AI.",
        page_category="services",
        page_title="Services",
        intent="help",
        stage="discover",
        llm_json_fn=bad_llm,
    )
    assert chips == []
    settings_mod.get_settings.cache_clear()


def test_generate_llm_suggested_replies_parses_chips(monkeypatch):
    from app.config import settings as settings_mod

    monkeypatch.setenv("LLM_SUGGESTED_REPLIES_ENABLED", "true")
    settings_mod.get_settings.cache_clear()

    def fake_llm(_prompt: str) -> dict:
        return {
            "chips": [
                "Tell me about agentic AI",
                "Tell me about agentic AI",
                "How do you deploy LLM agents?",
            ]
        }

    chips = generate_llm_suggested_replies(
        user_query="What do you do?",
        answer="Mobcoder AI builds production LLM agents.",
        page_category="ai_agents",
        page_title="Agentic AI",
        intent="help",
        stage="discover",
        llm_json_fn=fake_llm,
    )
    assert len(chips) == 2
    assert "Tell me about agentic AI" in chips
    settings_mod.get_settings.cache_clear()
