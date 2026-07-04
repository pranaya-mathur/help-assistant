from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class VisitorMeta(BaseModel):
    """Passive fingerprint captured by the widget before the first message."""

    timezone: str = Field(default="", max_length=64)
    language: str = Field(default="", max_length=32)
    scroll_depth_pct: int = Field(default=0, ge=0, le=100)
    utm_source: str = Field(default="", max_length=256)
    utm_medium: str = Field(default="", max_length=256)
    utm_campaign: str = Field(default="", max_length=256)
    utm_term: str = Field(default="", max_length=256)
    utm_content: str = Field(default="", max_length=256)
    referrer: str = Field(default="", max_length=2000)


class LeadProfileInput(BaseModel):
    name: str = ""
    email: str = ""
    company: str = ""
    project_need: str = ""
    project_type: str = ""
    timeline: str = ""
    budget_band: str = ""
    role: str = ""
    industry: str = ""
    decision_maker: bool = False


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[str] = None
    request_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="Optional caller-provided request trace id.",
    )
    page_url: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="Current page URL for category-aware retrieval.",
    )
    page_title: Optional[str] = Field(
        default=None,
        max_length=256,
        description="document.title from the host page for exact page context.",
    )
    referrer: Optional[str] = Field(default=None, max_length=2000)
    lead_consent: bool = Field(
        default=False,
        description="True only when the visitor explicitly submitted lead details after consent copy.",
    )
    expose_internal_sales_metadata: bool = Field(
        default=False,
        description="Request internal sales metadata; honored only when server config allows it.",
    )
    conversation_history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Prior turns only. Omit for new chats — do not send Swagger placeholder values.",
    )
    lead_profile: LeadProfileInput = Field(default_factory=LeadProfileInput)
    visitor_meta: Optional[VisitorMeta] = Field(
        default=None,
        description="Passive fingerprint captured by the widget (UTM, timezone, language, scroll depth). Never contains PII.",
    )
    client_ip: Optional[str] = Field(
        default=None,
        max_length=45,
        description="Client IP forwarded by trusted reverse proxy. Used for IP-based company enrichment.",
    )
    stream: bool = Field(
        default=False,
        description="Request token streaming when server ENABLE_CHAT_STREAMING is true.",
    )


class CitationOut(BaseModel):
    page_title: str
    source_url: str
    citation_text: str
    score: float
    snippet: str = ""
    page_category: str = ""


class ChatResponse(BaseModel):
    response: str
    request_id: str
    intent: str
    page_category: str
    stage: Optional[str] = None
    lead_score: Optional[str] = None
    citations: list[CitationOut]
    risk_flags: list[str]
    needs_contact_info: bool
    profile_question: str
    ready_for_booking: bool
    session_id: str
    lead_profile: LeadProfileInput
    grounding_passed: bool = True
    grounding_rewritten: bool = False
    suggested_replies: list[str] = Field(default_factory=list)
    show_human_escalation: bool = False


class EscalateRequest(BaseModel):
    session_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., min_length=3, max_length=254)
    message: str = Field(..., min_length=1, max_length=2000)
    page_url: Optional[str] = Field(default=None, max_length=2000)
    lead_consent: bool = Field(default=False)


class EscalateResponse(BaseModel):
    ok: bool = True
    session_id: str
    message: str = "We've received your message. A team member will reach out within 1 business day."


AnalyticsEventType = Literal[
    "widget_opened",
    "widget_opened_no_message",
    "cta_clicked",
    "message_sent",
    "qualify_shown",
    "qualify_submitted",
    "booking_cta_clicked",
    "conversation_abandoned",
    "human_escalation_requested",
    "help_answered",
    "sales_answered",
    "lead_qualified",
    "booking_intent_detected",
    "answer_not_found",
    "grounding_failure",
    "injection_blocked",
    "crm_dispatch_queued",
    "crm_dispatch_failed",
    "crm_dispatch_success",
    "feedback_positive",
    "feedback_negative",
    "exit_intent_shown",
    "proactive_trigger_shown",
]


class FeedbackRequest(BaseModel):
    session_id: Optional[str] = None
    message_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Stable ID for the bot message being rated (e.g. request_id of that turn).",
    )
    rating: int = Field(
        ...,
        description="1 for thumbs-up, -1 for thumbs-down.",
    )
    comment: str = Field(default="", max_length=1000)

    def validate_rating(self) -> "FeedbackRequest":
        if self.rating not in (1, -1):
            raise ValueError("rating must be 1 or -1")
        return self


class FeedbackResponse(BaseModel):
    ok: bool = True
    session_id: str
    message_id: str


class EventRequest(BaseModel):
    session_id: Optional[str] = None
    event: AnalyticsEventType
    cta: Optional[str] = Field(default=None, max_length=128)
    page_url: Optional[str] = Field(default=None, max_length=2000)
    page_category: Optional[str] = Field(default=None, max_length=64)
    visitor_meta: Optional[VisitorMeta] = Field(
        default=None,
        description="Passive fingerprint (same shape as chat requests).",
    )


class EventResponse(BaseModel):
    ok: bool = True
    session_id: str


class WidgetContextResponse(BaseModel):
    page_category: str
    opener: str
    starter_chips: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    vector_store_count: int
    vector_db_type: str
    chunks: int = 0
    last_crawl_at: Optional[str] = None
    last_ingest_at: Optional[str] = None


class SourceEntry(BaseModel):
    url: str
    title: str
    page_category: str
    chunk_count: int


class SourcesResponse(BaseModel):
    total_chunks: int
    sources: list[SourceEntry]


class CategoriesResponse(BaseModel):
    categories: list[str]


# ── Internal feedback read API ──────────────────────────────────────────────

class FeedbackRecord(BaseModel):
    """Single enriched feedback row returned by GET /feedback."""
    session_id: str
    message_id: str
    rating: int
    comment: str = ""
    created_at: str
    # Enriched from session at read time
    intent: str = ""
    stage: str = ""
    page_url: str = ""
    company: str = ""
    user_message: str = ""
    assistant_message: str = ""
    # True when user_message/assistant_message were matched to the rated turn by
    # its stored request_id. False means the session predates that field and the
    # displayed turn is a best-effort guess (most recent exchange), not confirmed.
    exact_match: bool = True


class FeedbackListResponse(BaseModel):
    total: int
    records: list[FeedbackRecord]


class FeedbackStatsResponse(BaseModel):
    total: int
    positive: int
    negative: int
    score: float  # positive / total, 0.0–1.0


class LeadRecord(BaseModel):
    """Single session with a captured name or email, returned by GET /leads."""
    session_id: str
    lead_profile: dict
    intent: str = ""
    stage: str = ""
    updated_at: str = ""


class LeadListResponse(BaseModel):
    total: int
    records: list[LeadRecord]
