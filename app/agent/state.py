from __future__ import annotations

from typing import Any, TypedDict


class LeadProfile(TypedDict, total=False):
    name: str
    email: str
    company: str
    project_need: str
    project_type: str
    timeline: str
    budget_band: str
    role: str
    industry: str
    decision_maker: bool


class Citation(TypedDict, total=False):
    chunk_id: str
    page_title: str
    source_url: str
    citation_text: str
    score: float
    snippet: str
    page_category: str


class AgentState(TypedDict, total=False):
    user_query: str
    request_id: str
    conversation_history: list[dict[str, str]]

    intent: str
    page_category: str
    prior_intent: str  # session intent from previous turn (continuity)
    stage: str  # discover | educate | qualify | convert
    lead_score: str  # hot | warm | cold
    lead_score_numeric: int
    meeting_readiness: str  # not_ready | maybe_ready | ready | booking_requested

    lead_profile: dict[str, Any]
    missing_fields: list[str]
    profile_question: str
    needs_contact_info: bool
    ready_for_booking: bool

    retrieved_chunks: list[dict[str, Any]]
    answer: str
    citations: list[Citation]

    risk_flags: list[str]
    is_safe: bool
    grounding_passed: bool
    grounding_rewritten: bool
    unsupported_claims: list[str]

    final_response: str
    suggested_replies: list[str]
    show_human_escalation: bool
    page_url: str
    page_title: str
    current_page_chunks: list[dict[str, Any]]
    session_context: dict[str, Any]
    lead_acknowledged: bool
    mark_lead_acknowledged: bool
    error: str
    step_timings_ms: dict[str, float]


def default_state(
    user_query: str = "",
    conversation_history: list[dict[str, str]] | None = None,
    lead_profile: dict[str, Any] | None = None,
    prior_intent: str = "",
    page_url: str = "",
    page_title: str = "",
    request_id: str = "",
    lead_acknowledged: bool = False,
    session_context: dict[str, Any] | None = None,
) -> AgentState:
    return AgentState(
        user_query=user_query,
        request_id=request_id,
        conversation_history=conversation_history or [],
        lead_profile=lead_profile or {},
        intent="",
        page_category="general",
        prior_intent=prior_intent,
        stage="discover",
        lead_score="cold",
        lead_score_numeric=0,
        meeting_readiness="not_ready",
        missing_fields=[],
        profile_question="",
        needs_contact_info=False,
        ready_for_booking=False,
        retrieved_chunks=[],
        answer="",
        citations=[],
        risk_flags=[],
        is_safe=True,
        final_response="",
        grounding_passed=True,
        grounding_rewritten=False,
        unsupported_claims=[],
        suggested_replies=[],
        show_human_escalation=False,
        page_url=page_url,
        page_title=page_title,
        current_page_chunks=[],
        session_context=session_context or {},
        lead_acknowledged=lead_acknowledged,
        mark_lead_acknowledged=False,
        error="",
        step_timings_ms={},
    )
