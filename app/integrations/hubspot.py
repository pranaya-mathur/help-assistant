from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0
_HTTP_TIMEOUT_SECONDS = 8.0

# ────────────────────────────────────────────────────────────────
# F4: Deal-stage mapping  (lead_score_numeric → HubSpot dealstage)
# These string values must match your HubSpot pipeline's internal
# stage IDs exactly — update if your pipeline uses different IDs.
# ────────────────────────────────────────────────────────────────
_DEALSTAGE_MAP = [
    # (min_score_inclusive, stage_id, stage_label)
    (70, "sqlqualified",      "SQL – Hot"),
    (31, "qualifiedtobuy",    "MQL – Warm"),
    (0,  "appointmentscheduled", "Lead – Cold"),
]


def _map_lead_score_to_dealstage(lead_score_numeric: int) -> dict[str, str]:
    """Return HubSpot deal-stage fields derived from numeric lead score."""
    for min_score, stage_id, label in _DEALSTAGE_MAP:
        if lead_score_numeric >= min_score:
            return {"dealstage": stage_id, "dealstage_label": label}
    return {"dealstage": "appointmentscheduled", "dealstage_label": "Lead – Cold"}


def _build_payload(
    lead_profile: dict[str, Any],
    intent: str,
    session_id: str,
    lead_score: str,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    ctx = context or {}
    numeric_score: int = int(ctx.get("lead_score_numeric") or 0)
    deal_stage = _map_lead_score_to_dealstage(numeric_score)
    return {
        "source": ctx.get("source") or "mobcoder_sales_assistant",
        "intent": intent,
        "session_id": session_id,
        "request_id": ctx.get("request_id", ""),
        "lead_score": lead_score,
        "lead_score_numeric": numeric_score,
        "dealstage": deal_stage["dealstage"],
        "dealstage_label": deal_stage["dealstage_label"],
        "meeting_readiness": ctx.get("meeting_readiness", "not_ready"),
        "response_stage": ctx.get("response_stage", ""),
        "page_url": ctx.get("page_url", ""),
        "first_page_url": ctx.get("first_page_url", ""),
        "last_page_url": ctx.get("last_page_url", ""),
        "referrer": ctx.get("referrer", ""),
        "utm_params": ctx.get("utm_params") or {},
        "lead_consent": bool(ctx.get("lead_consent", False)),
        "created_at": ctx.get("created_at", ""),
        "conversation_summary": ctx.get("conversation_summary") or "",
        "escalation_message": ctx.get("escalation_message") or "",
        "project_type": lead_profile.get("project_type", "unknown"),
        "role": lead_profile.get("role", "unknown"),
        "industry": lead_profile.get("industry", "unknown"),
        "decision_maker": bool(lead_profile.get("decision_maker", False)),
        "lead": {
            "name": lead_profile.get("name", ""),
            "email": lead_profile.get("email", ""),
            "company": lead_profile.get("company", ""),
            "project_need": lead_profile.get("project_need", ""),
            "project_type": lead_profile.get("project_type", "unknown"),
            "timeline": lead_profile.get("timeline", ""),
            "budget_band": lead_profile.get("budget_band", ""),
            "role": lead_profile.get("role", "unknown"),
            "industry": lead_profile.get("industry", "unknown"),
            "decision_maker": bool(lead_profile.get("decision_maker", False)),
        },
    }


def _post_once(url: str, payload: dict[str, Any]) -> bool:
    with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
        resp = client.post(url, json=payload)
        resp.raise_for_status()
    return True


def notify_qualified_lead(
    lead_profile: dict[str, Any],
    intent: str,
    session_id: str = "",
    lead_score: str = "",
    context: dict[str, Any] | None = None,
) -> bool:
    """
    POST qualified lead to HubSpot webhook URL when configured.
    Retries with exponential backoff. Returns True if dispatched successfully.
    """
    settings = get_settings()
    url = settings.hubspot_webhook_url
    if not url:
        logger.debug("HubSpot webhook not configured (HUBSPOT_WEBHOOK_URL)")
        return False

    payload = _build_payload(lead_profile, intent, session_id, lead_score, context)

    for attempt in range(_MAX_ATTEMPTS):
        try:
            _post_once(url, payload)
            logger.info(
                "HubSpot webhook dispatched session=%s request_id=%s intent=%s lead_score=%s",
                session_id,
                payload.get("request_id", ""),
                intent,
                lead_score,
            )
            return True
        except Exception as exc:
            logger.warning(
                "HubSpot webhook attempt %s/%s failed session=%s: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                session_id,
                exc,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2 ** attempt))
    return False


def dispatch_qualified_lead_async(
    lead_profile: dict[str, Any],
    intent: str,
    session_id: str = "",
    lead_score: str = "",
    context: dict[str, Any] | None = None,
    *,
    on_queued: Callable[[], None] | None = None,
    on_success: Callable[[], None] | None = None,
    on_failure: Callable[[], None] | None = None,
) -> None:
    """Fire-and-forget CRM dispatch — never blocks the chat response path."""

    def _worker() -> None:
        if on_queued:
            try:
                on_queued()
            except Exception:
                pass
        from app.integrations.apollo import enrich_lead_profile_from_apollo

        enriched_profile = enrich_lead_profile_from_apollo(lead_profile)
        success = notify_qualified_lead(
            lead_profile=enriched_profile,
            intent=intent,
            session_id=session_id,
            lead_score=lead_score,
            context=context,
        )
        if success:
            if on_success:
                try:
                    on_success()
                except Exception:
                    pass
        else:
            logger.error(
                "HubSpot webhook failed after retries session=%s request_id=%s",
                session_id,
                (context or {}).get("request_id", ""),
            )
            if on_failure:
                try:
                    on_failure()
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True, name="crm-dispatch").start()
