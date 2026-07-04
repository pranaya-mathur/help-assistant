from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0
_HTTP_TIMEOUT_SECONDS = 10.0

# Column order matters: the Apps Script writes values in this sequence and the
# sheet header row must match (see docs/GOOGLE_SHEETS_LEAD_LOG.md).
LEAD_ROW_FIELDS = (
    "logged_at",
    "session_id",
    "name",
    "email",
    "company",
    "role",
    "industry",
    "project_type",
    "project_need",
    "timeline",
    "budget_band",
    "lead_bucket",
    "score_total",
    "score_fit",
    "score_intent",
    "score_value",
    "cta_type",
    "needs_human_review",
    "page_url",
    "score_reasons",
)


def build_lead_row(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    profile = dict(state.get("lead_profile") or {})
    qual = dict(state.get("qualification_score") or {})
    return {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "name": str(profile.get("name") or ""),
        "email": str(profile.get("email") or ""),
        "company": str(profile.get("company") or ""),
        "role": str(profile.get("role") or ""),
        "industry": str(profile.get("industry") or ""),
        "project_type": str(profile.get("project_type") or ""),
        "project_need": str(profile.get("project_need") or ""),
        "timeline": str(profile.get("timeline") or ""),
        "budget_band": str(profile.get("budget_band") or ""),
        "lead_bucket": str(qual.get("bucket") or state.get("lead_score") or "cold"),
        "score_total": int(qual.get("total") or 0),
        "score_fit": int(qual.get("fit") or 0),
        "score_intent": int(qual.get("intent") or 0),
        "score_value": int(qual.get("value") or 0),
        "cta_type": str(state.get("cta_type") or ""),
        "needs_human_review": bool(state.get("needs_human_review")),
        "page_url": str(state.get("page_url") or ""),
        "score_reasons": "; ".join(qual.get("reasons") or []),
    }


def log_lead_row(state: dict[str, Any], session_id: str) -> bool:
    """POST a lead row to the Google Sheets Apps Script endpoint when configured.

    The Apps Script upserts by session_id, so repeat calls for the same session
    update the row in place (score refreshes as the conversation progresses).
    Blocking with retries — callers run this off the chat hot path.
    """
    settings = get_settings()
    url = settings.google_sheets_webhook_url
    if not url:
        logger.debug("Google Sheets webhook not configured (GOOGLE_SHEETS_WEBHOOK_URL)")
        return False

    payload = {"action": "upsert_lead", "row": build_lead_row(state, session_id)}
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(
                timeout=_HTTP_TIMEOUT_SECONDS, follow_redirects=True
            ) as client:
                # follow_redirects: Apps Script web apps 302 to a one-time
                # script.googleusercontent.com URL on success.
                resp = client.post(url, json=payload)
                resp.raise_for_status()
            logger.info("Google Sheets lead row upserted session=%s", session_id)
            return True
        except Exception as exc:
            logger.warning(
                "Google Sheets log attempt %s/%s failed session=%s: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                session_id,
                exc,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2 ** attempt))
    return False
