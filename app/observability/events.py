from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

CLIENT_EVENTS = frozenset({
    "widget_opened",
    "widget_opened_no_message",
    "cta_clicked",
    "message_sent",
    "qualify_shown",
    "qualify_submitted",
    "booking_cta_clicked",
    "conversation_abandoned",
    "human_escalation_requested",
    "feedback_positive",
    "feedback_negative",
    "exit_intent_shown",
    "proactive_trigger_shown",
})

SERVER_EVENTS = frozenset({
    "help_answered",
    "sales_answered",
    "lead_qualified",
    "message_sent",
    "qualify_shown",
    "booking_intent_detected",
    "answer_not_found",
    "grounding_failure",
    "injection_blocked",
    "crm_dispatch_queued",
    "crm_dispatch_failed",
    "crm_dispatch_success",
})

ALL_EVENTS = CLIENT_EVENTS | SERVER_EVENTS


def _deliver_webhook(url: str, body: dict[str, Any]) -> None:
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(url, json=body)
    except Exception as exc:
        logger.debug("analytics webhook failed: %s", exc)


def emit_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """Emit an analytics event (stdout + optional async webhook)."""
    body = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **(payload or {}),
    }
    line = json.dumps(body, default=str)
    logger.info("analytics %s", line)

    try:
        from app.infra.postgres import persist_analytics_event

        persist_analytics_event(body)
    except Exception as exc:
        logger.debug("analytics postgres persist skipped: %s", exc)

    url = get_settings().analytics_webhook_url
    if not url:
        return

    t = threading.Thread(target=_deliver_webhook, args=(url, body), daemon=True)
    t.start()


def _sanitize_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    except Exception:
        return None


def _base_payload(
    session_id: str,
    page_url: str | None = None,
    page_category: str | None = None,
    *,
    request_id: str | None = None,
    intent: str | None = None,
    project_type: str | None = None,
    lead_score_label: str | None = None,
    meeting_readiness: str | None = None,
    utm_params: dict[str, str] | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"session_id": session_id}
    clean_page_url = _sanitize_url(page_url)
    if clean_page_url:
        out["page_url"] = clean_page_url
    if page_category:
        out["page_category"] = page_category
    if request_id:
        out["request_id"] = request_id
    if intent:
        out["intent"] = intent
    if project_type:
        out["project_type"] = project_type
    if lead_score_label:
        out["lead_score_label"] = lead_score_label
    if meeting_readiness:
        out["meeting_readiness"] = meeting_readiness
    if utm_params:
        out["utm_params"] = utm_params
    if client_ip:
        out["client_ip"] = client_ip[:45]
    return out


def help_answered(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    utm_params: dict[str, str] | None = None,
    **extra: Any,
) -> None:
    emit_event(
        "help_answered",
        {
            **_base_payload(
                session_id,
                page_url,
                page_category,
                utm_params=utm_params,
            ),
            **extra,
        },
    )


def sales_answered(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    utm_params: dict[str, str] | None = None,
    **extra: Any,
) -> None:
    emit_event(
        "sales_answered",
        {
            **_base_payload(
                session_id,
                page_url,
                page_category,
                utm_params=utm_params,
            ),
            **extra,
        },
    )


def lead_qualified(
    session_id: str,
    intent: str,
    lead_score: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    project_type: str | None = None,
    meeting_readiness: str | None = None,
    request_id: str | None = None,
    utm_params: dict[str, str] | None = None,
) -> None:
    emit_event(
        "lead_qualified",
        {
            **_base_payload(
                session_id,
                page_url,
                page_category,
                request_id=request_id,
                intent=intent,
                project_type=project_type,
                lead_score_label=lead_score,
                meeting_readiness=meeting_readiness,
                utm_params=utm_params,
            ),
            "lead_score": lead_score,
        },
    )


def message_sent(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    request_id: str | None = None,
    intent: str | None = None,
    project_type: str | None = None,
    lead_score_label: str | None = None,
    meeting_readiness: str | None = None,
    utm_params: dict[str, str] | None = None,
    client_ip: str | None = None,
    **extra: Any,
) -> None:
    emit_event(
        "message_sent",
        {
            **_base_payload(
                session_id,
                page_url,
                page_category,
                request_id=request_id,
                intent=intent,
                project_type=project_type,
                lead_score_label=lead_score_label,
                meeting_readiness=meeting_readiness,
                utm_params=utm_params,
                client_ip=client_ip,
            ),
            **extra,
        },
    )


def qualify_shown(
    session_id: str,
    intent: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    utm_params: dict[str, str] | None = None,
    **extra: Any,
) -> None:
    emit_event(
        "qualify_shown",
        {
            **_base_payload(
                session_id,
                page_url,
                page_category,
                intent=intent,
                utm_params=utm_params,
            ),
            **extra,
        },
    )


def booking_intent_detected(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    request_id: str | None = None,
    utm_params: dict[str, str] | None = None,
) -> None:
    emit_event(
        "booking_intent_detected",
        _base_payload(
            session_id,
            page_url,
            page_category,
            request_id=request_id,
            intent="booking",
            utm_params=utm_params,
        ),
    )


def answer_not_found(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    request_id: str | None = None,
    intent: str | None = None,
    utm_params: dict[str, str] | None = None,
) -> None:
    emit_event(
        "answer_not_found",
        _base_payload(
            session_id,
            page_url,
            page_category,
            request_id=request_id,
            intent=intent,
            utm_params=utm_params,
        ),
    )


def grounding_failure(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    request_id: str | None = None,
    utm_params: dict[str, str] | None = None,
) -> None:
    emit_event(
        "grounding_failure",
        _base_payload(
            session_id,
            page_url,
            page_category,
            request_id=request_id,
            utm_params=utm_params,
        ),
    )


def injection_blocked(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
    request_id: str | None = None,
    utm_params: dict[str, str] | None = None,
) -> None:
    emit_event(
        "injection_blocked",
        _base_payload(
            session_id,
            page_url,
            page_category,
            request_id=request_id,
            utm_params=utm_params,
        ),
    )


def crm_dispatch_queued(
    session_id: str,
    *,
    request_id: str | None = None,
    lead_score_label: str | None = None,
    meeting_readiness: str | None = None,
    project_type: str | None = None,
) -> None:
    emit_event(
        "crm_dispatch_queued",
        _base_payload(
            session_id,
            request_id=request_id,
            lead_score_label=lead_score_label,
            meeting_readiness=meeting_readiness,
            project_type=project_type,
        ),
    )


def crm_dispatch_success(
    session_id: str,
    *,
    request_id: str | None = None,
) -> None:
    emit_event(
        "crm_dispatch_success",
        _base_payload(session_id, request_id=request_id),
    )


def crm_dispatch_failed(
    session_id: str,
    *,
    request_id: str | None = None,
) -> None:
    emit_event(
        "crm_dispatch_failed",
        _base_payload(session_id, request_id=request_id),
    )


def widget_opened(
    session_id: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
) -> None:
    emit_event("widget_opened", _base_payload(session_id, page_url, page_category))


def cta_clicked(
    session_id: str,
    cta: str,
    *,
    page_url: str | None = None,
    page_category: str | None = None,
) -> None:
    emit_event(
        "cta_clicked",
        {**_base_payload(session_id, page_url, page_category), "cta": cta},
    )


def handle_client_event(
    event_type: str,
    session_id: str,
    *,
    cta: str | None = None,
    page_url: str | None = None,
    page_category: str | None = None,
    utm_params: dict[str, str] | None = None,
    client_ip: str | None = None,
) -> None:
    base = _base_payload(
        session_id,
        page_url,
        page_category,
        utm_params=utm_params,
        client_ip=client_ip,
    )
    if event_type == "widget_opened":
        emit_event("widget_opened", base)
    elif event_type == "widget_opened_no_message":
        emit_event("widget_opened_no_message", base)
    elif event_type == "booking_cta_clicked" and cta:
        emit_event("booking_cta_clicked", {**base, "cta": cta})
    elif event_type == "cta_clicked" and cta:
        emit_event("cta_clicked", {**base, "cta": cta})
    else:
        emit_event(
            event_type,
            {
                **base,
                **({"cta": cta} if cta else {}),
            },
        )
