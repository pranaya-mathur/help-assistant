from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from app.api.schemas import ChatRequest, VisitorMeta
from app.crawler.page_loader import infer_page_category


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


def build_session_context(
    *,
    session_metadata: dict[str, Any] | None,
    request: ChatRequest | None = None,
    page_url: str = "",
) -> dict[str, Any]:
    """Assemble journey and attribution signals for the agent prompt."""
    meta = dict(session_metadata or {})
    visitor: VisitorMeta | None = request.visitor_meta if request else None

    utm = dict(meta.get("utm_params") or {})
    if request and request.page_url:
        for key, value in _extract_utm_params(request.page_url).items():
            utm.setdefault(key, value)

    ctx: dict[str, Any] = {
        "first_page_url": str(meta.get("first_page_url") or page_url or ""),
        "last_page_url": str(meta.get("last_page_url") or page_url or ""),
        "utm_source": utm.get("utm_source") or (visitor.utm_source if visitor else "") or "",
        "utm_medium": utm.get("utm_medium") or (visitor.utm_medium if visitor else "") or "",
        "utm_campaign": utm.get("utm_campaign") or (visitor.utm_campaign if visitor else "") or "",
        "referrer": (
            (request.referrer if request and request.referrer else "")
            or (visitor.referrer if visitor else "")
            or str(meta.get("referrer") or "")
        ),
        "scroll_depth_pct": int(
            (visitor.scroll_depth_pct if visitor else 0)
            or meta.get("visitor_scroll_depth_pct")
            or 0
        ),
        "visitor_timezone": str(meta.get("visitor_timezone") or (visitor.timezone if visitor else "") or ""),
        "visitor_language": str(meta.get("visitor_language") or (visitor.language if visitor else "") or ""),
    }
    ctx["first_page_category"] = infer_page_category(ctx["first_page_url"]) if ctx["first_page_url"] else "general"
    ctx["current_page_category"] = infer_page_category(page_url) if page_url else "general"
    return ctx
