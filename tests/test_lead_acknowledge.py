"""Lead acknowledgment and help-first UX after contact capture."""

from app.agent.lead_extractor import has_contact_capture, should_append_qualification
from app.agent.nodes import (
    _normalize_booking_links,
    _strip_sources_markdown,
    append_qualification,
)
from app.agent.suggested_replies import build_suggested_replies


def test_has_contact_capture():
    assert not has_contact_capture({})
    assert has_contact_capture({"name": "Saniya", "email": "s@mobcoder.com"})


def test_no_qualify_after_contact_captured():
    profile = {"name": "Saniya", "email": "s@mobcoder.com"}
    history = [
        {"role": "user", "content": "What services?"},
        {"role": "assistant", "content": "AI services."},
        {"role": "user", "content": "Pricing?"},
        {"role": "assistant", "content": "Flexible models."},
    ]
    assert not should_append_qualification("sales", history, profile, stage="qualify")
    assert not should_append_qualification("help", history, profile, stage="qualify")


def test_thanks_only_once_when_lead_acknowledged():
    profile = {"name": "Saniya", "email": "s@mobcoder.com"}
    state = {
        "intent": "help",
        "conversation_history": [],
        "lead_profile": profile,
        "answer": "Mobcoder AI builds agentic systems.",
        "lead_acknowledged": True,
    }
    out = append_qualification(state)
    assert "follow up shortly" not in out["answer"].lower()
    assert "calendly.com" not in out["answer"].lower()


def test_thanks_once_then_mark_acknowledged():
    profile = {"name": "Saniya", "email": "s@mobcoder.com"}
    state = {
        "intent": "help",
        "conversation_history": [],
        "lead_profile": profile,
        "answer": "Mobcoder AI builds agentic systems.",
        "lead_acknowledged": False,
    }
    out = append_qualification(state)
    assert "Thanks, Saniya" in out["answer"]
    assert out.get("mark_lead_acknowledged") is True
    assert "calendly.com" not in out["answer"].lower()


def test_strip_duplicate_sources_markdown():
    text = "Answer here.\n\n**Sources**\n- [Page](https://mobcoder.ai/x)"
    stripped = _strip_sources_markdown(text)
    assert "Sources" not in stripped
    assert "Answer here." in stripped


def test_normalize_homepage_booking_link():
    text = "Book here: https://mobcoder.ai/"
    fixed = _normalize_booking_links(
        text, "https://calendly.com/hello-mobcoder/mobcoderai"
    )
    assert "calendly.com" in fixed
    assert "mobcoder.ai/" not in fixed


def test_strip_standalone_sources_heading():
    text = "Answer here.\n\n**Sources**\n\nMobcoder AI Case Studies"
    stripped = _strip_sources_markdown(text)
    assert "Sources" not in stripped
    assert "Answer here." in stripped


def test_widget_prefill_skips_thanks_on_first_message():
    from app.api.chat_service import _lead_acknowledged_for_request

    profile = {"name": "Saniya", "email": "s@mobcoder.com"}
    assert _lead_acknowledged_for_request({}, [], {}, profile) is True


def test_dedupe_citations_by_url():
    from app.agent.nodes import _dedupe_citations

    cites = [
        {"source_url": "https://mobcoder.ai/case-studies/x", "page_title": "A"},
        {"source_url": "https://mobcoder.ai/case-studies/x", "page_title": "B"},
        {"source_url": "https://mobcoder.ai/services", "page_title": "S"},
    ]
    out = _dedupe_citations(cites)  # type: ignore[arg-type]
    assert len(out) == 2


def test_suggested_replies_help_mode_after_contact():
    chips = build_suggested_replies(
        intent="help",
        stage="convert",
        page_category="services",
        profile_question="",
        needs_contact_info=False,
        lead_profile={"name": "Saniya", "email": "s@mobcoder.com"},
        lead_acknowledged=True,
    )
    assert chips
    assert not any("schedule a discovery call" in c.lower() for c in chips)
