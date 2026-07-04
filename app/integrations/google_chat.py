from __future__ import annotations

import html
import logging
import time
from typing import Any

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 1.0
_HTTP_TIMEOUT_SECONDS = 8.0

_BUCKET_EMOJI = {"hot": "🔥", "warm": "🌤️", "cold": "❄️"}


def _build_message(
    summary: str,
    *,
    session_id: str,
    qualification_score: dict[str, Any] | None = None,
    page_url: str = "",
) -> dict[str, Any]:
    qual = qualification_score or {}
    bucket = str(qual.get("bucket") or "hot")
    emoji = _BUCKET_EMOJI.get(bucket, "🔔")
    title = f"{emoji} Lead flagged for human review"
    subtitle = f"{bucket.upper()} — {qual.get('total', '?')}/100"

    # Google Chat textParagraph accepts limited HTML; summary lines are plain
    # text from build_human_review_summary, so escape then restore line breaks.
    body = html.escape(summary[:3800]).replace("\n", "<br>")
    context_bits = [f"Session: {session_id}"]
    if page_url:
        context_bits.append(f"Page: {page_url}")

    return {
        "text": f"{title} — {subtitle}",  # fallback for plain-text surfaces
        "cardsV2": [
            {
                "cardId": f"lead-review-{session_id or 'unknown'}",
                "card": {
                    "header": {"title": title, "subtitle": subtitle},
                    "sections": [
                        {
                            "widgets": [
                                {"textParagraph": {"text": body}},
                                {
                                    "textParagraph": {
                                        "text": "<i>" + " · ".join(context_bits) + "</i>"
                                    }
                                },
                            ]
                        }
                    ],
                },
            }
        ],
    }


def notify_human_review(
    summary: str,
    *,
    session_id: str = "",
    qualification_score: dict[str, Any] | None = None,
    page_url: str = "",
) -> bool:
    """POST a human-review lead alert to a Google Chat space when configured.

    Blocking with retries — callers run this off the chat hot path.
    Returns True if delivered.
    """
    settings = get_settings()
    url = settings.google_chat_webhook_url
    if not url:
        logger.debug("Google Chat webhook not configured (GOOGLE_CHAT_WEBHOOK_URL)")
        return False

    payload = _build_message(
        summary,
        session_id=session_id,
        qualification_score=qualification_score,
        page_url=page_url,
    )
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
            logger.info("Google Chat human-review alert sent session=%s", session_id)
            return True
        except Exception as exc:
            logger.warning(
                "Google Chat alert attempt %s/%s failed session=%s: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                session_id,
                exc,
            )
            if attempt < _MAX_ATTEMPTS - 1:
                time.sleep(_BASE_BACKOFF_SECONDS * (2 ** attempt))
    return False
