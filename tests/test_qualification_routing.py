from __future__ import annotations

from app.agent.lead_intelligence import compute_lead_scoring, prioritize_missing_fields
from app.agent.retrieval_plan import check_retrieval_slug_dependencies
from app.agent.routing import (
    CTA_EDUCATE,
    CTA_HUMAN_HANDOFF,
    CTA_INSTANT_BOOKING,
    CTA_QUALIFY,
    build_human_review_summary,
    decide_cta,
    decide_routing,
    needs_human_review,
)

HOT_PROFILE = {
    "name": "Jane Doe",
    "email": "jane@acme.com",
    "company": "Acme Health",
    "project_need": "HIPAA-compliant patient portal",
    "project_type": "healthcare_fintech",
    "timeline": "asap",
    "budget_band": "$200k",
    "role": "cto",
    "industry": "healthcare",
    "decision_maker": True,
}


def _scored_state(profile: dict, intent: str = "sales", **overrides) -> dict:
    scoring = compute_lead_scoring(profile, intent, user_query=overrides.pop("user_query", ""))
    state = {
        "user_query": "",
        "lead_profile": profile,
        "intent": intent,
        "stage": "qualify",
        **scoring,
    }
    state.update(overrides)
    return state


class TestQualificationScoreDecomposition:
    def test_components_sum_to_total_when_unclamped(self):
        scoring = compute_lead_scoring(
            {"project_need": "mobile app", "project_type": "mobile_app"}, "sales"
        )
        qual = scoring["qualification_score"]
        assert qual["fit"] + qual["intent"] + qual["value"] == qual["total"]
        assert qual["total"] == scoring["lead_score_numeric"]

    def test_hot_lead_scores_hot_with_reasons(self):
        scoring = compute_lead_scoring(HOT_PROFILE, "booking")
        qual = scoring["qualification_score"]
        assert qual["bucket"] == "hot"
        assert scoring["lead_score"] == "hot"
        assert qual["fit"] > 0 and qual["intent"] > 0 and qual["value"] > 0
        assert any("budget" in r for r in qual["reasons"])
        assert any("decision-maker" in r for r in qual["reasons"])

    def test_empty_profile_is_cold(self):
        scoring = compute_lead_scoring({}, "general")
        qual = scoring["qualification_score"]
        assert qual["bucket"] == "cold"
        assert qual["total"] == 0
        assert qual["reasons"] == []

    def test_help_mode_cap_keeps_bucket_from_total(self):
        scoring = compute_lead_scoring(dict(HOT_PROFILE, email="", name=""), "help")
        qual = scoring["qualification_score"]
        assert qual["total"] <= 30
        assert qual["bucket"] == "cold"

    def test_backward_compatible_keys_present(self):
        scoring = compute_lead_scoring(HOT_PROFILE, "sales")
        assert set(scoring) >= {"lead_score", "lead_score_numeric", "meeting_readiness"}


class TestRouting:
    def test_booking_request_routes_to_instant_booking(self):
        state = _scored_state(HOT_PROFILE, intent="booking")
        assert decide_cta(state) == CTA_INSTANT_BOOKING

    def test_human_request_overrides_everything(self):
        state = _scored_state(HOT_PROFILE, intent="booking", user_query="let me talk to a human")
        state["user_query"] = "let me talk to a human"
        assert decide_cta(state) == CTA_HUMAN_HANDOFF

    def test_warm_lead_routes_to_qualify(self):
        state = _scored_state(
            {"project_need": "mobile app", "project_type": "mobile_app", "email": "a@b.com"},
            intent="sales",
        )
        assert state["lead_score"] == "warm"
        assert decide_cta(state) == CTA_QUALIFY

    def test_cold_visitor_gets_educate(self):
        state = _scored_state({}, intent="general")
        state["stage"] = "discover"
        assert decide_cta(state) == CTA_EDUCATE


class TestHumanReview:
    def test_hot_enterprise_lead_flags_review(self):
        state = _scored_state(HOT_PROFILE, intent="booking")
        assert needs_human_review(state) is True
        routing = decide_routing(state)
        assert routing["needs_human_review"] is True
        assert "jane@acme.com" in routing["human_review_summary"]

    def test_no_email_never_flags(self):
        state = _scored_state(dict(HOT_PROFILE, email=""), intent="booking")
        assert needs_human_review(state) is False

    def test_cold_lead_never_flags(self):
        state = _scored_state({}, intent="general")
        assert needs_human_review(state) is False
        assert decide_routing(state)["human_review_summary"] == ""

    def test_summary_contains_score_breakdown(self):
        state = _scored_state(HOT_PROFILE, intent="booking")
        summary = build_human_review_summary(state)
        assert "fit" in summary and "value" in summary
        assert "Acme Health" in summary


class TestPrioritizeMissingFields:
    def test_zero_value_probes_budget_first(self):
        qual = {"fit": 30, "intent": 30, "value": 0}
        missing = ["project_need", "timeline", "budget_band", "name", "email"]
        out = prioritize_missing_fields(missing, qual)
        assert out[0] == "budget_band"
        # contact fields keep their original slots
        assert out[3:] == ["name", "email"]

    def test_zero_intent_probes_timeline_first(self):
        qual = {"fit": 30, "intent": 0, "value": 20}
        out = prioritize_missing_fields(["project_need", "timeline", "budget_band"], qual)
        assert out[0] == "timeline"

    def test_no_score_keeps_intent_order(self):
        missing = ["project_need", "timeline", "budget_band"]
        assert prioritize_missing_fields(missing, {}) == missing
        assert prioritize_missing_fields(missing, None) == missing

    def test_contact_only_missing_is_untouched(self):
        missing = ["name", "email"]
        assert prioritize_missing_fields(missing, {"fit": 0, "intent": 0, "value": 0}) == missing


class TestCrawlDriftGuard:
    def test_all_dependencies_present_no_warnings(self):
        urls = [
            "https://mobcoder.ai/generative-ai-development-services",
            "https://mobcoder.ai/case-studies/gov-gig",
        ]
        assert check_retrieval_slug_dependencies(urls) == []

    def test_missing_pricing_page_warns(self):
        urls = ["https://mobcoder.ai/case-studies/gov-gig", "https://mobcoder.ai/blog/x"]
        warnings = check_retrieval_slug_dependencies(urls)
        assert len(warnings) == 1
        assert "pricing_preferred_pages" in warnings[0]

    def test_empty_crawl_warns_for_every_group(self):
        warnings = check_retrieval_slug_dependencies([])
        assert len(warnings) == 2
