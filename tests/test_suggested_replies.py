from app.agent.lead_extractor import should_append_qualification
from app.agent.suggested_replies import (
    MAX_SUGGESTIONS,
    build_suggested_replies,
    contextual_opener,
    merge_suggested_replies,
)


def test_help_discover_suggestions():
    replies = build_suggested_replies(
        intent="help",
        stage="discover",
        page_category="general",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
    )
    assert len(replies) >= 2
    assert not any("email" in r.lower() for r in replies)


def test_sales_first_turn_no_email_chip():
    replies = build_suggested_replies(
        intent="sales",
        stage="educate",
        page_category="general",
        profile_question="What is the best email to reach you?",
        needs_contact_info=False,
        lead_profile={},
    )
    assert not should_append_qualification("sales", [], {})
    assert not any("email to reach" in r.lower() for r in replies)


def test_qualify_shows_profile_question_chip():
    replies = build_suggested_replies(
        intent="help",
        stage="qualify",
        page_category="general",
        profile_question="May I have your name so our team can follow up?",
        needs_contact_info=True,
        lead_profile={},
    )
    assert any("name" in r.lower() for r in replies)


def test_convert_stage_booking():
    replies = build_suggested_replies(
        intent="sales",
        stage="convert",
        page_category="general",
        profile_question="",
        needs_contact_info=False,
        lead_profile={"name": "A", "email": "a@b.com", "company": "Co", "project_need": "AI", "timeline": "Q3", "budget_band": "50k"},
    )
    assert any("discovery call" in r.lower() for r in replies)


def test_contextual_opener_ai():
    text = contextual_opener("ai_agents")
    assert "AI" in text


def test_page_title_suggestion_chip():
    replies = build_suggested_replies(
        intent="help",
        stage="discover",
        page_category="case_studies",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
        page_url="https://mobcoder.ai/case-studies/fintech-wallet",
        page_title="Fintech Wallet Case Study | Mobcoder AI",
    )
    assert any("Fintech Wallet Case Study" in r for r in replies)


def test_journey_chip_case_studies_to_pricing():
    replies = build_suggested_replies(
        intent="help",
        stage="discover",
        page_category="pricing",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
        session_context={
            "first_page_category": "case_studies",
            "current_page_category": "pricing",
        },
    )
    assert any("price projects like" in r.lower() for r in replies)


def test_max_suggestions_cap():
    replies = build_suggested_replies(
        intent="help",
        stage="discover",
        page_category="general",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
    )
    assert len(replies) <= MAX_SUGGESTIONS


def test_timeline_choice_chips_when_project_need_known():
    replies = build_suggested_replies(
        intent="sales",
        stage="qualify",
        page_category="general",
        profile_question="",
        needs_contact_info=False,
        lead_profile={"project_need": "AI chatbot", "name": "A", "email": "a@b.com"},
    )
    assert any("3 months" in r for r in replies)
    assert any("exploring" in r.lower() for r in replies)


def test_budget_choice_chips_when_timeline_known():
    replies = build_suggested_replies(
        intent="sales",
        stage="qualify",
        page_category="general",
        profile_question="",
        needs_contact_info=False,
        lead_profile={
            "project_need": "AI chatbot",
            "timeline": "Q3",
            "name": "A",
            "email": "a@b.com",
        },
    )
    assert any("$50k" in r for r in replies)
    assert any("Not sure yet" in r for r in replies)
