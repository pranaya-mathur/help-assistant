from app.agent.prompts import format_answer_user_prompt
from app.agent.retrieval_plan import resolve_retrieval_plan


def test_prompt_includes_page_url_and_title():
    prompt = format_answer_user_prompt(
        intent="help",
        stage="discover",
        page_category="case_studies",
        user_query="hi",
        context="[1] Case study content",
        page_url="https://mobcoder.ai/case-studies/fintech-wallet",
        page_title="Fintech Wallet Case Study | Mobcoder AI",
    )
    assert "fintech-wallet" in prompt
    assert "Fintech Wallet Case Study" in prompt
    assert "Current page URL" in prompt


def test_prompt_includes_attribution_when_present():
    prompt = format_answer_user_prompt(
        intent="help",
        stage="discover",
        page_category="ai_agents",
        user_query="What do you build?",
        context="[1] AI agents",
        session_context={
            "utm_campaign": "ai-webinar",
            "utm_source": "google",
            "scroll_depth_pct": 60,
        },
    )
    assert "ai-webinar" in prompt
    assert "Attribution context" in prompt
    assert "never invent facts" in prompt.lower() or "tone/bridge only" in prompt


def test_prompt_omits_empty_attribution():
    prompt = format_answer_user_prompt(
        intent="general",
        stage="discover",
        page_category="general",
        user_query="hello",
        context="[1] Services",
        session_context={},
    )
    assert "Attribution context" not in prompt


def test_generic_query_uses_retrieval_plan_page_context():
    plan = resolve_retrieval_plan(
        user_query="tell me more",
        page_url="https://mobcoder.ai/case-studies/fintech-wallet",
        page_title="Fintech Wallet Case Study",
    )
    assert plan.include_current_page is True
    assert "fintech" in plan.search_query.lower()
    assert "Fintech Wallet" in plan.search_query
