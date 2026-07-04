from __future__ import annotations

from typing import Any

from app.agent.lead_extractor import (
    MIN_HELP_TURNS_BEFORE_QUALIFY,
    MIN_SALES_TURNS_BEFORE_QUALIFY,
    count_user_turns,
    get_missing_lead_fields,
    is_lead_complete,
)

ConversationStage = str  # discover | educate | qualify | convert


def compute_conversation_stage(
    intent: str,
    history: list[dict[str, str]] | None,
    lead_profile: dict[str, Any],
) -> ConversationStage:
    """
    discover  — first visits, general exploration
    educate   — answering product/service questions (help/sales)
    qualify   — rapport built; collecting lead fields
    convert   — lead complete; booking CTA
    """
    if is_lead_complete(lead_profile, intent):
        return "convert"

    user_turns = count_user_turns(history)

    if intent == "booking":
        missing = get_missing_lead_fields(lead_profile, "booking")
        return "qualify" if missing else "convert"

    if intent == "sales":
        if user_turns >= MIN_SALES_TURNS_BEFORE_QUALIFY:
            missing = get_missing_lead_fields(lead_profile, "sales")
            if missing:
                return "qualify"
        return "educate" if user_turns >= 1 else "discover"

    if intent == "help":
        if user_turns >= MIN_HELP_TURNS_BEFORE_QUALIFY:
            missing = get_missing_lead_fields(lead_profile, "help")
            if missing:
                return "qualify"
        return "educate" if user_turns >= 1 else "discover"

    return "discover"
