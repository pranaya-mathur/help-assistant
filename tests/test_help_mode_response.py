from app.agent.nodes import _filter_retrieved_chunks, build_final_response
from app.agent.retrieval_plan import RetrievalPlan


def test_help_mode_skips_pricing_contact_cta(monkeypatch):
    monkeypatch.setenv("OPERATING_MODE", "help")
    from app.config.settings import get_settings

    get_settings.cache_clear()

    state = {
        "user_query": "What about the Pricing?",
        "page_category": "pricing",
        "intent": "help",
        "answer": "We offer flexible engagement models.",
        "citations": [],
        "lead_profile": {},
        "conversation_history": [],
    }
    out = build_final_response(state)
    assert "Contact us for a tailored quote" not in out["final_response"]


def test_sales_mode_appends_pricing_contact_cta(monkeypatch):
    monkeypatch.setenv("OPERATING_MODE", "full")
    from app.config.settings import get_settings

    get_settings.cache_clear()

    state = {
        "user_query": "What about the Pricing?",
        "page_category": "pricing",
        "intent": "sales",
        "answer": "We offer flexible engagement models.",
        "citations": [],
        "lead_profile": {},
        "conversation_history": [],
    }
    out = build_final_response(state)
    assert "Contact us for a tailored quote" in out["final_response"]


def test_case_study_filter_drops_blog_posts():
    plan = RetrievalPlan(
        search_query="case studies",
        page_category="case_studies",
        exclude_source_tiers=(),
        include_current_page=False,
        prompt_page_category="case_studies",
    )
    chunks = [
        {"source_url": "https://mobcoder.ai/blog/agentic-ai-frameworks"},
        {"source_url": "https://mobcoder.ai/case-studies/gov-gig"},
        {"source_url": "https://mobcoder.ai/case-studies/nap-detect"},
    ]
    filtered = _filter_retrieved_chunks(chunks, plan)
    urls = [c["source_url"] for c in filtered]
    assert "https://mobcoder.ai/blog/agentic-ai-frameworks" not in urls
    assert "https://mobcoder.ai/case-studies/gov-gig" in urls


def test_pricing_filter_prefers_capabilities_overview():
    plan = RetrievalPlan(
        search_query="pricing",
        page_category="ai_agents",
        exclude_source_tiers=(),
        include_current_page=False,
        prompt_page_category="pricing",
    )
    chunks = [
        {"source_url": "https://mobcoder.ai/blog/pricing-tips"},
        {"source_url": "https://mobcoder.ai/generative-ai-development-services"},
        {"source_url": "https://mobcoder.ai/generative-ai-development-services"},
    ]
    filtered = _filter_retrieved_chunks(chunks, plan)
    assert "capabilities-overview" in filtered[0]["source_url"]
