from app.agent.prompts import _closing_instruction, format_answer_user_prompt


def test_qualify_stage_no_follow_up_question():
    closing = _closing_instruction("qualify", "sales")
    assert "Do NOT end with a question" in closing


def test_discover_stage_has_engagement_question():
    closing = _closing_instruction("discover", "help")
    assert "follow-up question" in closing


def test_format_includes_page_hint_for_ai():
    prompt = format_answer_user_prompt(
        intent="help",
        stage="discover",
        page_category="ai_agents",
        user_query="What AI services do you offer?",
        context="[1] Agentic AI services",
        page_url="https://mobcoder.ai/services/ai-agents",
        page_title="AI Agents | Mobcoder AI",
    )
    assert "co-pilot" in prompt or "agentic automation" in prompt
    assert "Direct answer" in prompt
    assert "What AI services do you offer?" in prompt
    assert "ai-agents" in prompt


def test_booking_prompt_uses_calendly_not_contact_page():
    prompt = format_answer_user_prompt(
        intent="sales",
        stage="discover",
        page_category="general",
        user_query="I want to book a call",
        context="[1] Discovery calls",
        booking_cta="Book a 30-minute discovery call with Mobcoder AI",
        contact_page_url="https://mobcoder.ai/contact-us",
        calendly_url="https://calendly.com/hello-mobcoder/mobcoderai",
    )
    assert "hello-mobcoder/mobcoderai" in prompt
    assert "contact-us" not in prompt.split("Booking when natural")[1]
