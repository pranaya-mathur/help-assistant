from __future__ import annotations

import hmac
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.attribution import persist_client_ip, persist_user_agent, persist_visitor_meta
from app.api.chat_service import (
    _extract_utm_params,
    _persist_session_attribution,
    _utm_from_context,
    get_widget_context,
    process_chat,
    process_escalate,
    stream_chat,
)
from app.api.schemas import (
    CategoriesResponse,
    ChatRequest,
    ChatResponse,
    EscalateRequest,
    EscalateResponse,
    EventRequest,
    EventResponse,
    FeedbackListResponse,
    FeedbackRecord,
    FeedbackRequest,
    FeedbackResponse,
    FeedbackStatsResponse,
    HealthResponse,
    LeadListResponse,
    LeadRecord,
    SourceEntry,
    SourcesResponse,
    WidgetContextResponse,
)
from app.crawler.page_loader import infer_page_category
from app.observability.events import CLIENT_EVENTS, handle_client_event
from app.sessions.store import get_session_store
from app.config.settings import get_settings
from app.rag.vector_store import get_vector_store

logger = logging.getLogger(__name__)
router = APIRouter()

PAGE_CATEGORIES = [
    "services",
    "ai_agents",
    "case_studies",
    "about",
    "contact",
    "careers",
    "blog",
    "general",
]


def _load_ingest_manifest() -> dict:
    path = Path(get_settings().ingest_manifest_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read ingest manifest: %s", exc)
        return {}


def _extract_client_ip(http_request: Request) -> str:
    """Extract real client IP respecting trust_proxy_headers setting."""
    settings = get_settings()
    if settings.trust_proxy_headers:
        # X-Forwarded-For: client, proxy1, proxy2 — take the leftmost (real client)
        forwarded_for = http_request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            ip = forwarded_for.split(",")[0].strip()
            if ip:
                return ip[:45]
        real_ip = http_request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip[:45]
    client = http_request.client
    return (client.host if client else "")[:45]


@router.get("/widget-context", response_model=WidgetContextResponse)
async def widget_context(page_url: str, page_title: str = "") -> WidgetContextResponse:
    url = (page_url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="page_url is required")
    data = get_widget_context(url, (page_title or "").strip())
    return WidgetContextResponse(**data)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
    # Inject client IP for Layer 2 IP enrichment (only if not already set by widget)
    # Never trust a client-supplied client_ip — always derive server-side
    # (Apollo enrichment / attribution would otherwise be spoofable by any caller).
    request = request.model_copy(update={"client_ip": _extract_client_ip(http_request)})
    try:
        return process_chat(
            request,
            user_agent=(http_request.headers.get("user-agent") or "")[:512],
        )
    except Exception as exc:
        logger.error(f"POST /chat error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent processing error. Please try again.")


@router.post("/events", response_model=EventResponse)
async def track_event(request: EventRequest, http_request: Request) -> EventResponse:
    store = get_session_store()
    session = store.get_or_create(request.session_id)
    client_ip = _extract_client_ip(http_request)
    persist_client_ip(store, session.session_id, client_ip)
    persist_user_agent(store, session.session_id, (http_request.headers.get("user-agent") or "")[:512])
    persist_visitor_meta(store, session.session_id, request.visitor_meta)
    page_url = (request.page_url or "").strip() or None
    page_category = (request.page_category or "").strip() or None
    if not page_category and page_url:
        page_category = infer_page_category(page_url)
    if request.event not in CLIENT_EVENTS:
        raise HTTPException(status_code=400, detail=f"Unsupported client event: {request.event}")

    metadata = dict(session.metadata)
    utm = _utm_from_context(page_url or "", metadata)
    if page_url:
        fresh_utm = _extract_utm_params(page_url)
        if fresh_utm:
            utm = fresh_utm
            _persist_session_attribution(
                store,
                session.session_id,
                page_url=page_url,
                referrer=metadata.get("referrer", ""),
                utm=fresh_utm,
            )

    handle_client_event(
        request.event,
        session.session_id,
        cta=request.cta,
        page_url=page_url,
        page_category=page_category,
        utm_params=utm or None,
        client_ip=client_ip or None,
    )
    return EventResponse(ok=True, session_id=session.session_id)


@router.post("/feedback", response_model=FeedbackResponse)
async def feedback(request: FeedbackRequest, http_request: Request) -> FeedbackResponse:
    """Record a thumbs-up (rating=1) or thumbs-down (rating=-1) for a bot response."""
    if request.rating not in (1, -1):
        raise HTTPException(status_code=422, detail="rating must be 1 or -1")
    store = get_session_store()
    session = store.get_or_create(request.session_id)
    client_ip = _extract_client_ip(http_request)
    persist_client_ip(store, session.session_id, client_ip)
    persist_user_agent(store, session.session_id, (http_request.headers.get("user-agent") or "")[:512])
    try:
        store.save_feedback(
            session_id=session.session_id,
            message_id=request.message_id,
            rating=request.rating,
            comment=request.comment or "",
        )
    except Exception as exc:
        logger.error("Feedback save error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not save feedback.")

    event = "feedback_positive" if request.rating == 1 else "feedback_negative"
    from app.observability.events import handle_client_event
    try:
        handle_client_event(
            event,
            session.session_id,
            cta=request.message_id,
            client_ip=client_ip or None,
        )
    except Exception as exc:
        logger.warning("Feedback analytics event failed: %s", exc)

    return FeedbackResponse(
        ok=True,
        session_id=session.session_id,
        message_id=request.message_id,
    )


@router.post("/escalate", response_model=EscalateResponse)
async def escalate(request: EscalateRequest, http_request: Request) -> EscalateResponse:
    client_ip = _extract_client_ip(http_request)
    user_agent = (http_request.headers.get("user-agent") or "")[:512]
    try:
        return process_escalate(request, client_ip=client_ip, user_agent=user_agent)
    except Exception as exc:
        logger.error("POST /escalate error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Escalation failed. Please try again.")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    # Never trust a client-supplied client_ip — always derive server-side
    # (Apollo enrichment / attribution would otherwise be spoofable by any caller).
    request = request.model_copy(update={"client_ip": _extract_client_ip(http_request)})
    try:
        return StreamingResponse(
            stream_chat(
                request,
                user_agent=(http_request.headers.get("user-agent") or "")[:512],
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        logger.error(f"POST /chat/stream error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail="Agent processing error. Please try again.")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    manifest = _load_ingest_manifest()
    try:
        count = get_vector_store().count()
        status = "ok"
    except Exception as exc:
        logger.error(f"Health check error: {exc}")
        count = -1
        status = "degraded"
    chunk_target = 100
    if status == "ok" and 0 < count < chunk_target:
        logger.warning(
            "Vector store below knowledge target: %s chunks (target %s)",
            count,
            chunk_target,
        )
    return HealthResponse(
        status=status,
        vector_store_count=count,
        vector_db_type=settings.vector_db_type,
        chunks=manifest.get("chunks", count if count > 0 else 0),
        last_crawl_at=manifest.get("last_crawl_at"),
        last_ingest_at=manifest.get("last_ingest_at"),
    )


@router.get("/sources", response_model=SourcesResponse)
async def sources() -> SourcesResponse:
    settings = get_settings()
    if not settings.source_endpoints_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    chunks_dir = Path(settings.chunks_data_dir)
    source_map: dict[str, SourceEntry] = {}
    total = 0

    if chunks_dir.exists():
        for json_file in sorted(chunks_dir.glob("chunks_*.json")):
            try:
                with open(json_file, encoding="utf-8") as fh:
                    items: list[dict] = json.load(fh)
                for c in items:
                    url = c.get("source_url", "")
                    if not url:
                        continue
                    if url not in source_map:
                        source_map[url] = SourceEntry(
                            url=url,
                            title=c.get("page_title", ""),
                            page_category=c.get("page_category", ""),
                            chunk_count=0,
                        )
                    source_map[url].chunk_count += 1
                    total += 1
            except Exception as exc:
                logger.warning(f"Could not read {json_file}: {exc}")

    return SourcesResponse(total_chunks=total, sources=list(source_map.values())[:200])


@router.get("/categories", response_model=CategoriesResponse)
async def categories() -> CategoriesResponse:
    if not get_settings().source_endpoints_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    return CategoriesResponse(categories=PAGE_CATEGORIES)


# ── Internal feedback read API ───────────────────────────────────────────────

def _require_internal_key(http_request: Request) -> None:
    """Raise 401 if INTERNAL_API_KEY is unset or header mismatches."""
    settings = get_settings()
    expected = (settings.internal_api_key or "").strip()
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    provided = http_request.headers.get("x-internal-key", "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _enrich_feedback_record(rec: dict, store) -> FeedbackRecord:
    """Join feedback row with session context (intent, profile, the rated turn)."""
    session_id = rec.get("session_id", "")
    session = store.get(session_id) if session_id else None
    user_msg = assistant_msg = ""
    page_url = company = intent = stage = ""
    exact_match = False

    if session:
        intent = session.intent or ""
        stage = session.stage or ""
        page_url = (session.metadata or {}).get("page_url", "")
        company = (session.lead_profile or {}).get("company", "")
        history = session.conversation_history or []
        message_id = rec.get("message_id", "")

        # Preferred path: the assistant turn was saved with its own request_id
        # (set on every /chat and /chat/stream call — see chat_service.save_turn),
        # which is exactly the id the widget reuses as feedback's message_id.
        # This is an exact match, not a guess.
        if message_id:
            for i in range(len(history) - 1, -1, -1):
                turn = history[i]
                if turn.get("role") == "assistant" and turn.get("request_id") == message_id:
                    assistant_msg = turn.get("content", "")
                    user_msg = history[i - 1].get("content", "") if i > 0 else ""
                    exact_match = True
                    break

        # Fallback for turns saved before this field existed: best-effort guess
        # at the most recent exchange. Only reachable for legacy data — flagged
        # via exact_match=False so it's not silently mistaken for the real turn.
        if not exact_match and len(history) >= 2:
            user_msg = history[-2].get("content", "") if history[-2].get("role") == "user" else ""
            assistant_msg = history[-1].get("content", "") if history[-1].get("role") == "assistant" else ""

    return FeedbackRecord(
        session_id=session_id,
        message_id=rec.get("message_id", ""),
        rating=int(rec.get("rating", 0)),
        comment=rec.get("comment", ""),
        created_at=rec.get("created_at", ""),
        intent=intent,
        stage=stage,
        page_url=page_url,
        company=company,
        user_message=user_msg[:500],
        assistant_message=assistant_msg[:1000],
        exact_match=exact_match,
    )


@router.get("/feedback", response_model=FeedbackListResponse)
async def list_feedback(
    http_request: Request,
    limit: int = 50,
    session_id: str | None = None,
) -> FeedbackListResponse:
    """Internal: list recent feedback records enriched with session context.
    Requires header: X-Internal-Key: <INTERNAL_API_KEY>
    """
    _require_internal_key(http_request)
    store = get_session_store()
    raw = store.list_feedback(limit=min(limit, 200), session_id=session_id or None)
    records = [_enrich_feedback_record(r, store) for r in raw]
    return FeedbackListResponse(total=len(records), records=records)


@router.get("/feedback/stats", response_model=FeedbackStatsResponse)
async def feedback_stats(http_request: Request) -> FeedbackStatsResponse:
    """Internal: aggregate thumbs totals.
    Requires header: X-Internal-Key: <INTERNAL_API_KEY>
    """
    _require_internal_key(http_request)
    stats = get_session_store().get_feedback_stats()
    return FeedbackStatsResponse(
        total=stats.get("total", 0),
        positive=stats.get("positive", 0),
        negative=stats.get("negative", 0),
        score=float(stats.get("score", 0.0)),
    )


@router.get("/leads", response_model=LeadListResponse)
async def list_leads(
    http_request: Request,
    limit: int = 100,
) -> LeadListResponse:
    """Internal: list recent sessions with a captured name or email.
    Requires header: X-Internal-Key: <INTERNAL_API_KEY>
    """
    _require_internal_key(http_request)
    raw = get_session_store().list_leads(limit=min(limit, 500))
    records = [
        LeadRecord(
            session_id=r.get("session_id", ""),
            lead_profile=r.get("lead_profile", {}),
            intent=r.get("intent", ""),
            stage=r.get("stage", ""),
            updated_at=r.get("updated_at", ""),
        )
        for r in raw
    ]
    return LeadListResponse(total=len(records), records=records)
