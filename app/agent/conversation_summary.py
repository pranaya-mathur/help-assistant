from __future__ import annotations

from typing import Any


def _first_user_question(history: list[dict[str, str]] | None, fallback: str = "") -> str:
    if history:
        for msg in history:
            if msg.get("role") == "user":
                content = str(msg.get("content") or "").strip()
                if content:
                    return content[:200]
    return fallback[:200] if fallback else ""


def build_conversation_summary(
    *,
    lead: dict[str, Any],
    history: list[dict[str, str]] | None = None,
    intent: str = "",
    user_query: str = "",
    page_url: str = "",
) -> str:
    """Deterministic sales summary — no LLM call."""
    parts: list[str] = []

    project_type = str(lead.get("project_type") or "").replace("_", " ")
    project_need = str(lead.get("project_need") or "").strip()
    if project_type and project_type != "unknown":
        parts.append(f"Project type: {project_type}.")
    if project_need:
        parts.append(f"Need: {project_need}.")

    timeline = str(lead.get("timeline") or "").strip()
    if timeline:
        parts.append(f"Timeline: {timeline}.")

    budget = str(lead.get("budget_band") or "").strip()
    if budget:
        parts.append(f"Budget: {budget}.")

    industry = str(lead.get("industry") or "").replace("_", " ")
    role = str(lead.get("role") or "").replace("_", " ")
    if industry and industry != "unknown":
        parts.append(f"Industry: {industry}.")
    if role and role != "unknown":
        parts.append(f"Role: {role}.")

    company = str(lead.get("company") or "").strip()
    name = str(lead.get("name") or "").strip()
    if company:
        parts.append(f"Company: {company}.")
    elif name:
        parts.append(f"Contact: {name}.")

    key_question = _first_user_question(history, user_query)
    if key_question:
        parts.append(f"Key question: {key_question}")

    if intent:
        parts.append(f"Intent: {intent}.")

    if page_url:
        parts.append(f"Page: {page_url.split('?')[0][:120]}.")

    summary = " ".join(parts).strip()
    if len(summary) < 20:
        summary = (
            f"Visitor exploring {project_need or 'MobCoder services'}. "
            f"Intent: {intent or 'general'}."
        )
    return summary[:600]
