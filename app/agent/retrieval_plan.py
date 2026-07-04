from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agent.page_context import is_generic_query, page_slug
from app.agent.intent_classifier import MOBCODER_TECH_TERMS

_TYPO_MAP = {
    "soultion": "solution",
    "lookin": "looking",
    "devlopment": "development",
    "chatbto": "chatbot",
    "mobcoder": "mobcoder",
}

_CITY_IN_QUERY_RE = re.compile(
    r"\b(in|near|based in|located in|from)\s+"
    r"(denver|sydney|seattle|austin|dallas|chicago|boston|nyc|new york|"
    r"los angeles|san francisco|toronto|london|dubai|india|bangalore)\b",
    re.I,
)

_TOPIC_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "ai_agents",
        re.compile(
            r"\b(rag|llm|agentic|chatbot|genai|machine learning|ai agent|"
            r"co-pilot|copilot|supply chain|workflow automation|department)\b",
            re.I,
        ),
    ),
    (
        "case_studies",
        re.compile(
            r"\b(case stud(y|ies)|portfolio|past project|client success|"
            r"projects you|nap detect|tifin|tread map)\b",
            re.I,
        ),
    ),
    (
        "services",
        re.compile(
            r"\b(mobile|flutter|react native|web app|fintech|healthcare|"
            r"staff aug|dedicated dev|document process|integration)\b",
            re.I,
        ),
    ),
    ("contact", re.compile(r"\b(discovery call|schedule|book a call|contact)\b", re.I)),
    ("pricing", re.compile(r"\b(pricing|price|cost|budget|quote|how much)\b", re.I)),
]


@dataclass(frozen=True)
class RetrievalPlan:
    search_query: str
    page_category: str | None
    exclude_source_tiers: tuple[str, ...]
    include_current_page: bool
    prompt_page_category: str | None = None


def _should_expand_rag_context(query: str) -> bool:
    """Only steer retrieval toward RAG when the visitor actually asks about it."""
    from app.agent.intent_classifier import RAG_QUERY_PATTERN, SOLUTION_NEED_PATTERN

    if RAG_QUERY_PATTERN.search(query):
        return True
    if re.search(r"\b(knowledge base|internal doc|sop|policy|document q&a)\b", query, re.I):
        return True
    return bool(SOLUTION_NEED_PATTERN.search(query))


def _normalize_query(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return text
    lowered = text.lower()
    for typo, fix in _TYPO_MAP.items():
        lowered = re.sub(rf"\b{re.escape(typo)}\b", fix, lowered)
    if _should_expand_rag_context(lowered):
        if "rag" not in lowered and "retrieval augmented" not in lowered:
            lowered = f"{lowered} retrieval augmented generation"
    return lowered


def infer_topic_category(query: str) -> str | None:
    q = (query or "").lower()
    for category, pattern in _TOPIC_RULES:
        if pattern.search(q):
            return category
    if any(term in q for term in MOBCODER_TECH_TERMS):
        return "ai_agents"
    return None


def _query_mentions_location(query: str) -> bool:
    return bool(_CITY_IN_QUERY_RE.search(query or ""))


def resolve_retrieval_plan(
    *,
    user_query: str,
    page_url: str = "",
    page_title: str = "",
    history: list[dict[str, str]] | None = None,
) -> RetrievalPlan:
    del history  # reserved for follow-up context; hot path stays rule-only
    normalized = _normalize_query(user_query)
    topic = infer_topic_category(normalized)

    exclude: list[str] = []
    if not _query_mentions_location(normalized):
        exclude.append("seo_geo")

    include_page = is_generic_query(user_query)
    if topic and topic != "general":
        include_page = False

    search_query = normalized
    page_category = topic
    prompt_page_category = topic
    if topic == "pricing":
        # Capabilities overview (ai_agents) holds engagement/pricing language.
        page_category = "ai_agents"
        prompt_page_category = "pricing"
        search_query = f"{normalized} generative ai agentic capabilities engagement models pricing quote".strip()
    elif topic == "case_studies":
        search_query = f"{normalized} nap detect tifin tread gov gig client success".strip()

    if include_page:
        slug = page_slug(page_url)
        if slug and slug not in search_query.lower():
            search_query = f"{search_query} {slug}".strip()
        if page_title and page_title.lower() not in search_query.lower():
            search_query = f"{search_query} {page_title}".strip()

    return RetrievalPlan(
        search_query=search_query,
        page_category=page_category,
        exclude_source_tiers=tuple(exclude),
        include_current_page=include_page,
        prompt_page_category=prompt_page_category,
    )
