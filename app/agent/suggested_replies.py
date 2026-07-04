from __future__ import annotations

import re
from typing import Any

from app.agent.page_context import is_specific_page_title, page_slug, slug_to_chip_hint
from app.config.settings import get_settings

MAX_SUGGESTIONS = 4

_PRICING_QUERY_RE = re.compile(
    r"\b(pricing|price|cost|quote|budget|rates?|how much|engagement models?)\b",
    re.I,
)


def _is_pricing_context(page_category: str, user_query: str) -> bool:
    if page_category == "pricing":
        return True
    return bool(_PRICING_QUERY_RE.search(user_query))


def build_suggested_replies(
    *,
    intent: str,
    stage: str,
    page_category: str,
    profile_question: str,
    needs_contact_info: bool,
    lead_profile: dict[str, Any],
    citations: list[dict[str, Any]] | None = None,
    show_human_escalation: bool = False,
    user_query: str = "",
    lead_acknowledged: bool = False,
    page_url: str = "",
    page_title: str = "",
    session_context: dict[str, Any] | None = None,
) -> list[str]:
    """Heuristic follow-up chips — no extra LLM call."""
    settings = get_settings()
    suggestions: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        t = text.strip()
        if not t or t.lower() in seen or len(suggestions) >= MAX_SUGGESTIONS:
            return
        seen.add(t.lower())
        suggestions.append(t)

    has_contact = bool(
        (lead_profile.get("name") or "").strip() and (lead_profile.get("email") or "").strip()
    )

    _add_page_context_suggestions(
        add,
        page_url=page_url,
        page_title=page_title,
        page_category=page_category,
        citations=citations,
    )
    _add_journey_suggestions(add, page_category=page_category, session_context=session_context)
    _add_structured_qualify_choices(add, intent=intent, lead_profile=lead_profile)

    # Help/general: after contact is known, prefer educational chips (incl. convert stage).
    if has_contact and (intent in ("help", "general") or lead_acknowledged):
        add("Can you share Mobcoder AI case studies?")
        add("What AI and agentic AI services does Mobcoder AI offer?")
        add("How does Mobcoder AI approach pricing?")
        if show_human_escalation:
            add("Talk to our team")
        return suggestions[:MAX_SUGGESTIONS]

    if stage == "convert" or intent == "booking":
        add("I'd like to schedule a discovery call.")
        if settings.calendly_url:
            add(settings.booking_cta)
        if show_human_escalation:
            add("Talk to our team")
        return suggestions[:MAX_SUGGESTIONS]

    if _is_pricing_context(page_category, user_query):
        add("Contact us for a tailored quote")
        add("What engagement models does Mobcoder AI offer?")
        add("I'd like to book a discovery call.")
        return suggestions[:MAX_SUGGESTIONS]

    if show_human_escalation:
        add("Talk to our team")

    if needs_contact_info and profile_question:
        short = profile_question
        if len(short) > 72:
            short = short[:69] + "..."
        add(short)

    if intent == "help":
        if stage == "discover":
            add("Can you share Mobcoder AI case studies?")
            add("How does Mobcoder AI work with clients on a typical project?")
            add("What AI and agentic AI services does Mobcoder AI offer?")
        else:
            add("What is Mobcoder AI's development process?")
            add("Book a discovery call with Mobcoder AI")
            _add_category_suggestions(add, page_category, citations)

    elif intent == "sales":
        if not lead_profile.get("project_need"):
            add("We're exploring an AI chatbot for customer support.")
            add("Do you offer team augmentation or dedicated developers?")
        add("I'd like to book a discovery call.")
        _add_category_suggestions(add, page_category, citations)

    elif intent == "general":
        add("What services does Mobcoder AI offer?")
        add("Tell me about Mobcoder AI's AI capabilities.")
        add("I'd like to schedule a discovery call.")

    return suggestions[:MAX_SUGGESTIONS]


def merge_suggested_replies(
    heuristic: list[str],
    llm_chips: list[str] | None,
    *,
    max_count: int = MAX_SUGGESTIONS,
) -> list[str]:
    """Merge heuristic chips (priority) with optional LLM chips; dedupe and cap."""
    out: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        t = (text or "").strip()
        if not t or t.lower() in seen or len(out) >= max_count:
            return
        seen.add(t.lower())
        out.append(t)

    for chip in heuristic:
        add(chip)
    for chip in llm_chips or []:
        add(chip)
    return out


def _add_structured_qualify_choices(
    add: Any,
    *,
    intent: str,
    lead_profile: dict[str, Any],
) -> None:
    """One-tap timeline/budget chips parsed by lead_extractor."""
    if intent not in ("sales", "booking"):
        return
    if not (lead_profile.get("project_need") or "").strip():
        return
    if not (lead_profile.get("timeline") or "").strip():
        add("We need to launch in the next 3 months")
        add("Timeline is 3–6 months")
        add("Just exploring for now")
        return
    if not (lead_profile.get("budget_band") or "").strip():
        add("Budget under $50k")
        add("$50k–$150k")
        add("$150k+")
        add("Not sure yet")


def _add_page_context_suggestions(
    add: Any,
    *,
    page_url: str,
    page_title: str,
    page_category: str,
    citations: list[dict[str, Any]] | None,
) -> None:
    title = (page_title or "").strip()
    if is_specific_page_title(title):
        short = title.split("|")[0].strip()
        if len(short) <= 72:
            add(f"Tell me more about {short}.")
        return

    slug = page_slug(page_url)
    if page_category == "case_studies" and slug:
        hint = slug_to_chip_hint(slug)
        if hint:
            add(f"What outcomes did you achieve for {hint}?")

    if citations:
        cite_title = (citations[0].get("page_title") or "").strip()
        if cite_title and len(cite_title) < 60 and not cite_title.startswith("http"):
            add(f"Tell me more about {cite_title}.")


def _add_journey_suggestions(
    add: Any,
    *,
    page_category: str,
    session_context: dict[str, Any] | None,
) -> None:
    if not session_context:
        return
    first_cat = str(session_context.get("first_page_category") or "general")
    current_cat = page_category or str(session_context.get("current_page_category") or "general")
    if first_cat == current_cat or first_cat == "general":
        return
    if first_cat == "case_studies" and current_cat == "pricing":
        add("How do you price projects like the one I was reading?")
    elif first_cat == "services" and current_cat == "contact":
        add("I'd like to discuss the services I was exploring.")


def _add_category_suggestions(
    add: Any,
    page_category: str,
    citations: list[dict[str, Any]] | None,
) -> None:
    category_prompts = {
        "ai_agents": "Tell me more about Mobcoder AI's agentic AI work.",
        "case_studies": "Share a relevant Mobcoder AI case study.",
        "services": "What engagement models does Mobcoder AI offer?",
        "about": "Who is Mobcoder AI and what do you specialize in?",
        "pricing": "Contact us for a tailored quote",
    }
    if page_category in category_prompts:
        add(category_prompts[page_category])

    if citations:
        title = (citations[0].get("page_title") or "").strip()
        if title and len(title) < 60:
            add(f"Tell me more about {title}.")


def starter_chips_for_category(page_category: str) -> list[str]:
    """Category-specific starter chips shown when the widget first opens."""
    chips_by_category: dict[str, list[str]] = {
        "ai_agents": [
            "What AI and agentic systems does Mobcoder AI build?",
            "How do you deploy production LLM agents?",
            "Share a relevant AI case study.",
        ],
        "case_studies": [
            "Can you share Mobcoder AI case studies?",
            "What industries have you worked with?",
            "Tell me about a recent client project.",
        ],
        "services": [
            "What services does Mobcoder AI offer?",
            "Do you offer staff augmentation?",
            "How does Mobcoder AI price projects?",
        ],
        "pricing": [
            "How does Mobcoder AI approach pricing for custom software?",
            "What engagement models does Mobcoder AI offer?",
            "Contact us for a tailored quote",
        ],
        "contact": [
            "I'd like to schedule a discovery call.",
            "How can I reach the Mobcoder AI team?",
            "What happens on a discovery call?",
        ],
        "about": [
            "Who is Mobcoder AI?",
            "What does Mobcoder AI specialize in?",
            "How does Mobcoder AI partner with clients?",
        ],
    }
    default = [
        "What services does Mobcoder AI offer?",
        "Tell me about Mobcoder AI's AI capabilities.",
        "I'd like to schedule a discovery call.",
    ]
    return chips_by_category.get(page_category, default)[:MAX_SUGGESTIONS]


def contextual_opener(page_category: str) -> str:
    """Static greeting when widget opens on a known page category."""
    openers = {
        "ai_agents": (
            "Hi! You're on our AI page — I can explain agentic AI, chatbots, and how "
            "Mobcoder AI builds production-grade systems. What would you like to explore?"
        ),
        "case_studies": (
            "Hi! I can walk you through Mobcoder AI case studies and example projects. "
            "What industry or use case interests you?"
        ),
        "services": (
            "Hi! I can help with Mobcoder AI services, engagement models, and how we partner "
            "on custom software. What are you looking for?"
        ),
        "pricing": (
            "Hi! I can explain Mobcoder AI engagement models and how we price custom software "
            "and AI work — or connect you with our team for a tailored quote."
        ),
        "about": (
            "Hi! Ask me about Mobcoder AI's team, expertise, and how we work with clients."
        ),
        "contact": (
            "Hi! I can answer questions about Mobcoder AI or help you book a discovery call."
        ),
    }
    return openers.get(
        page_category,
        "Hi! I can help with Mobcoder AI services, AI projects, and booking a discovery call.",
    )
