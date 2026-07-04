from app.agent.nodes import _apply_page_category, _page_category_from_url
from app.agent.page_context import (
    is_generic_query,
    is_specific_page_title,
    normalize_page_url,
    page_slug,
    urls_match,
)


def test_normalize_page_url_strips_query_and_www():
    assert normalize_page_url("https://www.mobcoder.ai/case-studies/foo?utm=x") == (
        "https://mobcoder.ai/case-studies/foo"
    )


def test_page_slug_from_url():
    assert page_slug("https://mobcoder.ai/case-studies/fintech-wallet") == "fintech wallet"


def test_urls_match_normalized():
    assert urls_match(
        "https://www.mobcoder.ai/services/",
        "https://mobcoder.ai/services",
    )


def test_is_generic_query():
    assert is_generic_query("hi")
    assert is_generic_query("tell me more")
    assert not is_generic_query("What AI services does Mobcoder offer?")


def test_is_specific_page_title():
    assert is_specific_page_title("Fintech Wallet Case Study | Mobcoder AI")
    assert not is_specific_page_title("Mobcoder AI")


def test_page_category_from_ai_url():
    assert _page_category_from_url("https://mobcoder.ai/services/ai-agents") == "ai_agents"


def test_apply_page_category_generic_hi():
    state = {"user_query": "hi", "page_url": "https://mobcoder.ai/case-studies"}
    result = _apply_page_category(state, "general")
    assert result == "case_studies"


def test_apply_page_category_keeps_classifier_when_specific():
    state = {
        "user_query": "What AI and agentic AI services does MobCoder offer?",
        "page_url": "https://mobcoder.ai/contact",
    }
    result = _apply_page_category(state, "ai_agents")
    assert result == "ai_agents"
