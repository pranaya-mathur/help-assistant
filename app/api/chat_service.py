from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlparse, urlunparse

import asyncio

from app.agent.graph import run_agent
from app.agent.lead_extractor import count_user_turns, has_contact_capture, merge_profiles
from app.agent.stream_runner import run_agent_stream_events
from app.agent.post_response import dispatch_post_response_enrichment
from app.api.attribution import (
    persist_client_ip,
    persist_user_agent,
    persist_visitor_meta_from_chat,
)
from app.api.history import sanitize_conversation_history
from app.api.schemas import ChatRequest, ChatResponse, CitationOut, EscalateRequest, EscalateResponse, LeadProfileInput
from app.config.settings import get_settings
from app.crawler.page_loader import infer_page_category
from app.agent.conversation_summary import build_conversation_summary
from app.integrations.apollo import enrich_company_from_ip
from app.integrations.hubspot import dispatch_qualified_lead_async
from app.observability.events import (
    answer_not_found,
    booking_intent_detected,
    crm_dispatch_failed,
    crm_dispatch_queued,
    crm_dispatch_success,
    emit_event,
    grounding_failure,
    help_answered,
    injection_blocked,
    lead_qualified,
    message_sent,
    qualify_shown,
    sales_answered,
)
from app.api.session_context import build_session_context
from app.sessions.store import get_session_store

logger = logging.getLogger(__name__)


def _request_id_from_request(request: ChatRequest) -> str:
    raw = (request.request_id or "").strip()
    if raw:
        return raw[:128]
    return str(uuid.uuid4())


def _safe_message_hash(message: str) -> str:
    return sha256(message.encode("utf-8")).hexdigest()[:16]


def sanitize_url_for_logs(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return ""


def _utm_from_context(page_url: str, session_metadata: dict[str, Any] | None = None) -> dict[str, str]:
    stored = dict((session_metadata or {}).get("utm_params") or {})
    if stored:
        return stored
    return _extract_utm_params(page_url)


def _persist_session_attribution(
    store: Any,
    session_id: str,
    *,
    page_url: str,
    referrer: str,
    utm: dict[str, str],
) -> None:
    if page_url:
        store.update_metadata(session_id, page_url=page_url, referrer=referrer)
    if utm:
        store.patch_metadata(session_id, {"utm_params": utm})


def _extract_utm_params(url: str) -> dict[str, str]:
    try:
        query = parse_qs(urlparse(url).query)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for key in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        value = query.get(key, [""])[0]
        if value:
            out[key] = value[:256]
    return out


def _source_metadata(state: dict[str, Any]) -> tuple[list[str], list[float]]:
    chunks = list(state.get("retrieved_chunks") or [])
    urls: list[str] = []
    scores: list[float] = []
    for chunk in chunks[:5]:
        url = sanitize_url_for_logs(str(chunk.get("source_url") or ""))
        if url and url not in urls:
            urls.append(url)
        raw_score = chunk.get("rerank_score", chunk.get("score"))
        if raw_score is not None:
            try:
                scores.append(round(float(raw_score), 4))
            except (TypeError, ValueError):
                continue
    return urls, scores


def _citation_status(state: dict[str, Any]) -> str:
    if state.get("citations"):
        return "present"
    if state.get("retrieved_chunks"):
        return "missing_after_retrieval"
    return "not_applicable"


def _log_chat_result(
    *,
    event: str,
    request: ChatRequest,
    session_id: str,
    request_id: str,
    state: dict[str, Any],
    streaming: bool,
    latency_ms: float,
    error: Exception | None = None,
) -> None:
    source_urls, retrieval_scores = _source_metadata(state)
    payload: dict[str, Any] = {
        "event": event,
        "session_id": session_id,
        "request_id": request_id,
        "message_hash": _safe_message_hash(request.message),
        "intent": state.get("intent", ""),
        "response_stage": state.get("stage", "discover"),
        "lead_score": state.get("lead_score", "cold"),
        "retrieved_chunks": len(state.get("retrieved_chunks") or []),
        "selected_source_urls": source_urls,
        "retrieval_scores": retrieval_scores,
        "grounding_status": "passed" if state.get("grounding_passed", True) else "failed",
        "citation_validation_status": _citation_status(state),
        "latency_ms": round(latency_ms, 2),
        "step_timings_ms": state.get("step_timings_ms") or {},
        "model": get_settings().openai_model,
        "streaming": streaming,
    }
    if error is not None:
        payload["error_type"] = error.__class__.__name__
        payload["error_message"] = str(error)[:300]
        logger.error("chat_request_failed %s", json.dumps(payload, sort_keys=True), exc_info=True)
    else:
        logger.info("chat_request_completed %s", json.dumps(payload, sort_keys=True))


def _profile_from_request(raw: dict[str, Any]) -> dict[str, Any]:
    profile: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, str) and value.strip():
            profile[key] = value.strip()
    return profile


def _page_url_from_request(request: ChatRequest) -> str:
    return (request.page_url or "").strip()


def _page_title_from_request(request: ChatRequest) -> str:
    return (request.page_title or "").strip()[:256]


def _page_category_from_url(page_url: str) -> str:
    if not page_url:
        return "general"
    return infer_page_category(page_url)


def _fire_ip_enrichment_background(session_id: str, client_ip: str) -> None:
    """Layer 2: Async IP-to-company enrichment — fire-and-forget, never blocks response."""
    settings = get_settings()
    if not settings.apollo_ip_enrichment_enabled or not client_ip:
        return

    async def _run() -> None:
        try:
            result = await enrich_company_from_ip(client_ip)
            if not result:
                return
            store = get_session_store()
            session = store.get(session_id)
            if not session:
                return
            updates: dict[str, Any] = {"ip_enrichment": {**result, "source_ip": client_ip[:45]}}
            # Pre-seed lead profile with company/industry if not yet captured
            profile = dict(session.lead_profile)
            changed = False
            if result.get("company") and not profile.get("company"):
                profile["company"] = result["company"]
                changed = True
            if result.get("industry") and not profile.get("industry"):
                profile["industry"] = result["industry"]
                changed = True
            store.patch_metadata(session_id, updates)
            if changed:
                # Persist enriched profile — save_turn would overwrite; use patch
                store.patch_metadata(session_id, {"ip_enriched_profile": profile})
                logger.info(
                    "IP enrichment applied session=%s company=%s industry=%s",
                    session_id,
                    result.get("company"),
                    result.get("industry"),
                )
        except Exception as exc:
            logger.debug("IP enrichment background task error: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        # No running loop (sync context) — run in thread-safe way
        asyncio.run(_run())


def _merge_session_inputs(
    request: ChatRequest,
    *,
    user_agent: str = "",
) -> tuple[str, list[dict[str, str]], dict[str, Any], str, str, str, str, dict[str, Any], dict[str, Any]]:
    store = get_session_store()
    is_new_session = not request.session_id or store.get(request.session_id) is None
    session = store.get_or_create(request.session_id)
    session_id = session.session_id

    req_profile = _profile_from_request(request.lead_profile.model_dump())
    profile = merge_profiles(session.lead_profile, req_profile)

    if get_settings().trust_client_conversation_history and request.conversation_history:
        # Opt-in only (default off): a client-supplied history is trusted as-is here —
        # sanitize_conversation_history only checks role/content shape, not authenticity,
        # so a caller could otherwise inject fabricated prior turns to steer intent
        # classification, lead extraction, retrieval context, and CRM qualification.
        history = sanitize_conversation_history(
            [{"role": m.role, "content": m.content} for m in request.conversation_history]
        )
    else:
        history = sanitize_conversation_history(session.conversation_history)

    page_url = _page_url_from_request(request) or session.metadata.get("page_url", "")
    page_title = _page_title_from_request(request) or str(session.metadata.get("page_title") or "")
    if request.page_title:
        store.patch_metadata(session_id, {"page_title": page_title})
    utm = _extract_utm_params(page_url) if page_url else {}
    if request.page_url or utm:
        _persist_session_attribution(
            store,
            session_id,
            page_url=page_url,
            referrer=request.referrer or "",
            utm=utm,
        )
        session = store.get(session_id) or session

    # Layer 1: widget fingerprint (timezone, language, UTM, referrer)
    persist_visitor_meta_from_chat(store, session_id, request)

    if request.client_ip:
        persist_client_ip(store, session_id, request.client_ip)

    if user_agent:
        persist_user_agent(store, session_id, user_agent)

    # Layer 2: fire async IP-to-company enrichment on new sessions only
    if is_new_session and request.client_ip:
        _fire_ip_enrichment_background(session_id, request.client_ip)

    # Merge any IP-enriched profile fields (written async by previous request)
    ip_enriched = (session.metadata or {}).get("ip_enriched_profile") or {}
    if ip_enriched:
        profile = merge_profiles(ip_enriched, profile)  # explicit user data wins

    return (
        session_id,
        history,
        profile,
        session.intent,
        session.stage,
        page_url,
        page_title,
        dict(session.metadata),
        dict(session.lead_profile),
    )


def _citation_snippet_from_text(text: str, max_len: int = 140) -> str:
    raw = (text or "").strip()
    if raw.lower().startswith("source:"):
        raw = raw.split(":", 1)[-1].strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3].rstrip() + "..."


def _lead_acknowledged_for_request(
    session_metadata: dict[str, Any],
    history: list[dict[str, str]],
    profile_before: dict[str, Any],
    profile_after: dict[str, Any],
) -> bool:
    """True when we should not re-append the one-time thanks + Calendly block."""
    if session_metadata.get("lead_acknowledged"):
        return True
    if has_contact_capture(profile_before):
        return True
    # Widget pre-filled contact from localStorage on the first message — not a new capture.
    if has_contact_capture(profile_after) and count_user_turns(history) == 0:
        return True
    return False


def state_to_response(
    state: dict[str, Any],
    session_id: str,
    *,
    expose_internal_sales_metadata: bool = False,
) -> ChatResponse:
    raw_citations = state.get("citations", [])
    citations_out = [
        CitationOut(
            page_title=c.get("page_title", ""),
            source_url=c.get("source_url", ""),
            citation_text=c.get("citation_text", ""),
            score=round(float(c.get("score", 0.0)), 4),
            snippet=(c.get("snippet") or _citation_snippet_from_text(c.get("citation_text", ""))),
            page_category=str(c.get("page_category") or ""),
        )
        for c in raw_citations
    ]
    prof = state.get("lead_profile") or {}
    return ChatResponse(
        response=state.get("final_response", ""),
        request_id=state.get("request_id", ""),
        intent=state.get("intent", ""),
        page_category=state.get("page_category", "general"),
        stage=state.get("stage", "discover") if expose_internal_sales_metadata else None,
        lead_score=state.get("lead_score", "cold") if expose_internal_sales_metadata else None,
        citations=citations_out,
        risk_flags=state.get("risk_flags", []),
        needs_contact_info=bool(state.get("needs_contact_info", False)),
        profile_question=state.get("profile_question", ""),
        ready_for_booking=bool(state.get("ready_for_booking", False)),
        session_id=session_id,
        lead_profile=LeadProfileInput(
            name=prof.get("name") or "",
            email=prof.get("email") or "",
            company=prof.get("company") or "",
            project_need=prof.get("project_need") or "",
            project_type=prof.get("project_type") or "",
            timeline=prof.get("timeline") or "",
            budget_band=prof.get("budget_band") or "",
            role=prof.get("role") or "",
            industry=prof.get("industry") or "",
            decision_maker=bool(prof.get("decision_maker", False)),
        ),
        grounding_passed=bool(state.get("grounding_passed", True)),
        grounding_rewritten=bool(state.get("grounding_rewritten", False)),
        suggested_replies=list(state.get("suggested_replies") or []),
        show_human_escalation=bool(state.get("show_human_escalation", False)),
    )


_CRM_FINGERPRINT_FIELDS = (
    "email",
    "name",
    "company",
    "project_need",
    "project_type",
    "timeline",
    "budget_band",
    "role",
    "industry",
)


def _crm_dispatch_fingerprint(lead: dict[str, Any], intent: str) -> str:
    parts = [intent] + [str(lead.get(k) or "").strip().lower() for k in _CRM_FINGERPRINT_FIELDS]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _crm_already_dispatched(session_metadata: dict[str, Any], fingerprint: str) -> bool:
    if session_metadata.get("crm_dispatch_fingerprint") == fingerprint:
        return True
    if session_metadata.get("crm_dispatch_pending_fingerprint") == fingerprint:
        return True
    return False


def _mark_crm_dispatch_pending(
    session_id: str,
    fingerprint: str,
    *,
    request_id: str = "",
    intent: str = "",
    lead_score: str = "",
    lead_score_numeric: int = 0,
    meeting_readiness: str = "",
    payload: dict[str, Any] | None = None,
) -> None:
    store = get_session_store()
    store.patch_metadata(
        session_id,
        {
            "crm_dispatch_pending_fingerprint": fingerprint,
            "crm_dispatched_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    try:
        from app.infra.postgres import record_crm_dispatch_queued

        record_crm_dispatch_queued(
            session_id=session_id,
            fingerprint=fingerprint,
            request_id=request_id,
            intent=intent,
            lead_score=lead_score,
            lead_score_numeric=lead_score_numeric,
            meeting_readiness=meeting_readiness,
            payload=payload,
        )
    except Exception as exc:
        logger.debug("CRM dispatch postgres record skipped: %s", exc)


def _mark_crm_dispatch_success(session_id: str, fingerprint: str) -> None:
    get_session_store().patch_metadata(
        session_id,
        {
            "crm_dispatch_fingerprint": fingerprint,
            "crm_dispatch_pending_fingerprint": "",
        },
    )


def _clear_crm_dispatch_pending(session_id: str, fingerprint: str) -> None:
    data = get_session_store().get(session_id)
    if not data:
        return
    if data.metadata.get("crm_dispatch_pending_fingerprint") == fingerprint:
        get_session_store().patch_metadata(session_id, {"crm_dispatch_pending_fingerprint": ""})


def _crm_on_dispatch_success(session_id: str, request_id: str, fingerprint: str) -> None:
    crm_dispatch_success(session_id, request_id=request_id)
    _mark_crm_dispatch_success(session_id, fingerprint)
    try:
        from app.infra.postgres import update_crm_dispatch_status

        update_crm_dispatch_status(
            session_id=session_id,
            fingerprint=fingerprint,
            status="success",
        )
    except Exception as exc:
        logger.debug("CRM dispatch postgres update skipped: %s", exc)


def _crm_on_dispatch_failure(session_id: str, request_id: str, fingerprint: str) -> None:
    crm_dispatch_failed(session_id, request_id=request_id)
    _clear_crm_dispatch_pending(session_id, fingerprint)
    try:
        from app.infra.postgres import update_crm_dispatch_status

        update_crm_dispatch_status(
            session_id=session_id,
            fingerprint=fingerprint,
            status="failed",
            error_message="HubSpot webhook failed after retries",
        )
    except Exception as exc:
        logger.debug("CRM dispatch postgres update skipped: %s", exc)


def _should_dispatch_crm(state: dict[str, Any]) -> bool:
    """CRM handoff when lead is complete or high meeting-readiness with context."""
    settings = get_settings()
    if settings.is_help_mode() or not settings.enable_lead_qualification:
        return False

    if state.get("ready_for_booking"):
        return True

    readiness = str(state.get("meeting_readiness") or "not_ready")
    if readiness not in {"ready", "booking_requested"}:
        return False

    lead = state.get("lead_profile") or {}
    if not lead.get("email"):
        return False

    has_project = bool(lead.get("project_need")) or (
        lead.get("project_type") and str(lead.get("project_type")) != "unknown"
    )
    if state.get("intent") == "booking":
        return True
    return has_project


def _emit_post_chat_analytics(
    *,
    session_id: str,
    request: ChatRequest,
    state: dict[str, Any],
    request_id: str = "",
    session_metadata: dict[str, Any] | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> None:
    page_url = _page_url_from_request(request)
    page_category = state.get("page_category") or _page_category_from_url(page_url)
    intent = state.get("intent", "")
    response = state.get("final_response", "")
    lead_score = state.get("lead_score", "cold")
    lead = state.get("lead_profile") or {}
    utm = _utm_from_context(page_url, session_metadata)
    client_ip = (
        session_metadata.get("last_client_ip")
        or session_metadata.get("first_client_ip")
        or ""
    )

    message_sent(
        session_id,
        page_url=page_url or None,
        page_category=page_category,
        request_id=request_id,
        intent=intent,
        project_type=lead.get("project_type"),
        lead_score_label=lead_score,
        meeting_readiness=state.get("meeting_readiness"),
        utm_params=utm or None,
        client_ip=client_ip or None,
    )

    if intent == "booking":
        booking_intent_detected(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            request_id=request_id,
            utm_params=utm or None,
        )

    risk_flags = state.get("risk_flags") or []
    if "prompt_injection" in risk_flags:
        injection_blocked(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            request_id=request_id,
            utm_params=utm or None,
        )

    if intent in ("help", "sales") and not state.get("retrieved_chunks"):
        answer_not_found(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            request_id=request_id,
            intent=intent,
            utm_params=utm or None,
        )

    if state.get("grounding_passed") is False:
        grounding_failure(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            request_id=request_id,
            utm_params=utm or None,
        )

    if state.get("needs_contact_info"):
        qualify_shown(
            session_id,
            intent,
            page_url=page_url or None,
            page_category=page_category,
            utm_params=utm or None,
        )

    if intent == "help" and response:
        help_answered(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            utm_params=utm or None,
        )
    elif intent == "sales" and response:
        sales_answered(
            session_id,
            page_url=page_url or None,
            page_category=page_category,
            utm_params=utm or None,
        )

    if _should_dispatch_crm(state) and not request.lead_consent:
        logger.info(
            "CRM dispatch skipped (no lead_consent) session=%s intent=%s",
            session_id,
            intent,
        )
    elif _should_dispatch_crm(state):
        metadata = session_metadata or {}
        fingerprint = _crm_dispatch_fingerprint(lead, intent)
        if _crm_already_dispatched(metadata, fingerprint):
            logger.debug(
                "CRM dispatch skipped (already sent) session=%s fingerprint=%s",
                session_id,
                fingerprint,
            )
        else:
            summary = build_conversation_summary(
                lead=lead,
                history=conversation_history,
                intent=intent,
                user_query=request.message,
                page_url=page_url,
            )
            lead_context = {
                "request_id": request_id,
                "page_url": page_url,
                "first_page_url": metadata.get("first_page_url") or page_url,
                "last_page_url": metadata.get("last_page_url") or page_url,
                "referrer": request.referrer or metadata.get("referrer", ""),
                "utm_params": utm,
                "response_stage": state.get("stage", "discover"),
                "lead_consent": bool(request.lead_consent),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "conversation_summary": summary,
                "lead_score_numeric": state.get("lead_score_numeric", 0),
                "meeting_readiness": state.get("meeting_readiness", "not_ready"),
            }
            _mark_crm_dispatch_pending(
                session_id,
                fingerprint,
                request_id=request_id,
                intent=intent,
                lead_score=lead_score,
                lead_score_numeric=int(state.get("lead_score_numeric") or 0),
                meeting_readiness=str(state.get("meeting_readiness") or "not_ready"),
                payload=lead_context,
            )
            lead_qualified(
                session_id,
                intent,
                lead_score,
                page_url=page_url or None,
                page_category=page_category,
                project_type=lead.get("project_type"),
                meeting_readiness=state.get("meeting_readiness"),
                request_id=request_id,
                utm_params=utm or None,
            )
            dispatch_qualified_lead_async(
                lead_profile=lead,
                intent=intent,
                session_id=session_id,
                lead_score=lead_score,
                context=lead_context,
                on_queued=lambda: crm_dispatch_queued(
                    session_id,
                    request_id=request_id,
                    lead_score_label=lead_score,
                    meeting_readiness=state.get("meeting_readiness"),
                    project_type=lead.get("project_type"),
                ),
                on_success=lambda sid=session_id, rid=request_id, fp=fingerprint: _crm_on_dispatch_success(
                    sid, rid, fp
                ),
                on_failure=lambda sid=session_id, rid=request_id, fp=fingerprint: _crm_on_dispatch_failure(
                    sid, rid, fp
                ),
            )


def get_widget_context(page_url: str, page_title: str = "") -> dict[str, Any]:
    from app.agent.suggested_replies import contextual_opener, starter_chips_for_category

    category = infer_page_category(page_url, page_title)
    return {
        "page_category": category,
        "opener": contextual_opener(category),
        "starter_chips": starter_chips_for_category(category),
    }


def process_chat(request: ChatRequest, *, user_agent: str = "") -> ChatResponse:
    store = get_session_store()
    request_id = _request_id_from_request(request)
    started = time.perf_counter()
    session_id, history, profile, prior_intent, _, page_url, page_title, session_metadata, profile_before = _merge_session_inputs(
        request, user_agent=user_agent
    )
    session_context = build_session_context(
        session_metadata=session_metadata,
        request=request,
        page_url=page_url,
    )
    lead_ack = _lead_acknowledged_for_request(
        session_metadata, history, profile_before, profile
    )
    state: dict[str, Any] = {"request_id": request_id, "session_id": session_id}

    try:
        state = run_agent(
            user_query=request.message,
            conversation_history=history,
            lead_profile=profile,
            prior_intent=prior_intent or "",
            page_url=page_url,
            page_title=page_title,
            request_id=request_id,
            lead_acknowledged=lead_ack,
            session_context=session_context,
        )
    except Exception as exc:
        _log_chat_result(
            event="chat_request_failed",
            request=request,
            session_id=session_id,
            request_id=request_id,
            state=state,
            streaming=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=exc,
        )
        raise

    response = state.get("final_response", "")
    lead = state.get("lead_profile") or profile
    intent = state.get("intent", "")
    stage = state.get("stage", "discover")

    store.save_turn(
        session_id,
        request.message,
        response,
        lead,
        intent=intent,
        stage=stage,
        request_id=request_id,
    )
    if state.get("mark_lead_acknowledged"):
        store.patch_metadata(session_id, {"lead_acknowledged": True})
    elif lead_ack:
        store.patch_metadata(session_id, {"lead_acknowledged": True})

    _emit_post_chat_analytics(
        session_id=session_id,
        request=request,
        state=state,
        request_id=request_id,
        session_metadata=session_metadata,
        conversation_history=history,
    )
    _log_chat_result(
        event="chat_request_completed",
        request=request,
        session_id=session_id,
        request_id=request_id,
        state=state,
        streaming=False,
        latency_ms=(time.perf_counter() - started) * 1000,
    )

    expose_internal = (
        get_settings().expose_internal_sales_metadata
        and request.expose_internal_sales_metadata
    )
    return state_to_response(
        state,
        session_id,
        expose_internal_sales_metadata=expose_internal,
    )


async def stream_chat(request: ChatRequest, *, user_agent: str = "") -> AsyncIterator[str]:
    """SSE: token events when streaming enabled, else legacy single JSON blob."""
    settings = get_settings()
    use_token_stream = settings.enable_chat_streaming and request.stream

    if not use_token_stream:
        result = process_chat(request)
        payload = result.model_dump()
        yield f"data: {json.dumps(payload)}\n\n"
        yield "data: [DONE]\n\n"
        return

    store = get_session_store()
    request_id = _request_id_from_request(request)
    started = time.perf_counter()
    session_id, history, profile, prior_intent, _, page_url, page_title, session_metadata, profile_before = _merge_session_inputs(
        request, user_agent=user_agent
    )
    session_context = build_session_context(
        session_metadata=session_metadata,
        request=request,
        page_url=page_url,
    )
    lead_ack = _lead_acknowledged_for_request(
        session_metadata, history, profile_before, profile
    )
    expose_internal = (
        get_settings().expose_internal_sales_metadata
        and request.expose_internal_sales_metadata
    )

    final_state: dict[str, Any] = {}
    try:
        async for event in run_agent_stream_events(
            user_query=request.message,
            conversation_history=history,
            lead_profile=profile,
            prior_intent=prior_intent or "",
            page_url=page_url,
            page_title=page_title,
            request_id=request_id,
            lead_acknowledged=lead_ack,
            session_context=session_context,
        ):
            if event.get("type") == "done":
                final_state = event.get("state") or {}
                resp = state_to_response(
                    final_state,
                    session_id,
                    expose_internal_sales_metadata=expose_internal,
                )
                out = {
                    "type": "meta",
                    "data": {
                        k: v
                        for k, v in resp.model_dump().items()
                        if k != "response"
                    },
                }
                yield f"data: {json.dumps(out)}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'response': resp.response})}\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
    except Exception as exc:
        _log_chat_result(
            event="chat_request_failed",
            request=request,
            session_id=session_id,
            request_id=request_id,
            state=final_state or {"request_id": request_id},
            streaming=True,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=exc,
        )
        raise

    if final_state:
        response = final_state.get("final_response", "")
        lead = final_state.get("lead_profile") or profile
        store.save_turn(
            session_id,
            request.message,
            response,
            lead,
            intent=final_state.get("intent", ""),
            stage=final_state.get("stage", "discover"),
            request_id=request_id,
        )
        if final_state.get("mark_lead_acknowledged"):
            store.patch_metadata(session_id, {"lead_acknowledged": True})
        elif lead_ack:
            store.patch_metadata(session_id, {"lead_acknowledged": True})
        _emit_post_chat_analytics(
            session_id=session_id,
            request=request,
            state=final_state,
            request_id=request_id,
            session_metadata=session_metadata,
            conversation_history=history,
        )
        _log_chat_result(
            event="chat_request_completed",
            request=request,
            session_id=session_id,
            request_id=request_id,
            state=final_state,
            streaming=True,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        dispatch_post_response_enrichment(state=final_state, session_id=session_id)

    yield "data: [DONE]\n\n"


def process_escalate(
    request: EscalateRequest,
    *,
    client_ip: str = "",
    user_agent: str = "",
) -> EscalateResponse:
    store = get_session_store()
    session = store.get_or_create(request.session_id)
    session_id = session.session_id
    if client_ip:
        persist_client_ip(store, session_id, client_ip)
    if user_agent:
        persist_user_agent(store, session_id, user_agent)
    page_url = (request.page_url or "").strip()
    metadata = dict(session.metadata)
    utm = _utm_from_context(page_url, metadata)
    if page_url or utm:
        _persist_session_attribution(
            store,
            session_id,
            page_url=page_url,
            referrer=metadata.get("referrer", ""),
            utm=utm or _extract_utm_params(page_url),
        )

    lead_profile = {
        "name": request.name.strip(),
        "email": request.email.strip(),
        "project_need": request.message.strip(),
    }
    store.patch_metadata(session_id, {"human_escalation": True})
    emit_event(
        "human_escalation_requested",
        {
            "session_id": session_id,
            "page_url": sanitize_url_for_logs(page_url) or None,
            "utm_params": utm or {},
            "email": lead_profile["email"],
        },
    )

    lead_context = {
        "source": "human_escalation",
        "page_url": page_url,
        "first_page_url": metadata.get("first_page_url") or page_url,
        "last_page_url": metadata.get("last_page_url") or page_url,
        "referrer": metadata.get("referrer", ""),
        "utm_params": utm,
        "lead_consent": bool(request.lead_consent),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "conversation_summary": request.message.strip(),
        "escalation_message": request.message.strip(),
    }
    if request.lead_consent:
        dispatch_qualified_lead_async(
            lead_profile=lead_profile,
            intent="sales",
            session_id=session_id,
            lead_score="warm",
            context=lead_context,
        )
    else:
        logger.info("Escalation CRM dispatch skipped (no lead_consent) session=%s", session_id)
    return EscalateResponse(ok=True, session_id=session_id)
