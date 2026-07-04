from app.agent.retrieval_plan import resolve_retrieval_plan


def test_rag_typo_plan_excludes_geo():
    plan = resolve_retrieval_plan(
        user_query="I am lookin a soultion based out of RAG",
    )
    assert "seo_geo" in plan.exclude_source_tiers
    assert plan.page_category == "ai_agents"
    assert "rag" in plan.search_query.lower()


def test_location_query_allows_geo():
    plan = resolve_retrieval_plan(
        user_query="best ai company in denver",
    )
    assert "seo_geo" not in plan.exclude_source_tiers


def test_agentic_query_does_not_force_rag_expansion():
    plan = resolve_retrieval_plan(
        user_query="What AI and agentic systems does Mobcoder AI build?",
    )
    assert "retrieval augmented generation rag ai agent" not in plan.search_query.lower()
    assert plan.page_category == "ai_agents"


def test_supply_chain_copilot_topic():
    plan = resolve_retrieval_plan(
        user_query="Can you help me with a Co-Pilot for SupplyChain department?",
    )
    assert plan.page_category == "ai_agents"
    assert "retrieval augmented" not in plan.search_query.lower()


def test_case_study_query_routes_to_portfolio():
    plan = resolve_retrieval_plan(
        user_query="Can you share Mobcoder AI case studies?",
    )
    assert plan.page_category == "case_studies"
    assert plan.prompt_page_category == "case_studies"
    assert "nap detect" in plan.search_query.lower()


def test_pricing_query_targets_capabilities_overview():
    plan = resolve_retrieval_plan(user_query="What about the Pricing?")
    assert plan.page_category == "ai_agents"
    assert plan.prompt_page_category == "pricing"
    assert "capabilities-overview" in plan.search_query.lower()


def test_pricing_query_does_not_expand_rag():
    plan = resolve_retrieval_plan(user_query="How much does a project cost?")
    assert "retrieval augmented" not in plan.search_query.lower()
