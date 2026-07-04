from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache
from typing import Any

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_REWRITE_PROMPT = (
    "Rewrite the following visitor question as a concise search query for the "
    "MobCoder website (mobcoder.ai). Focus on services, AI, mobile, web, and "
    "engineering keywords. Output ONLY the rewritten query, no quotes or explanation.\n\n"
    "Question: {query}"
)

_SESSION_CACHE: dict[str, str] = {}
_MAX_CACHE = 512


def _cache_key(query: str, session_id: str = "") -> str:
    raw = f"{session_id}|{query.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _trim_query(text: str, max_len: int = 200) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3].rstrip() + "..."


def rewrite_query_for_retrieval(
    query: str,
    *,
    session_id: str = "",
    profile: dict[str, Any] | None = None,
    page_title: str = "",
    page_slug: str = "",
    llm_text_fn: Any = None,
    query_rewrite_enabled: bool | None = None,
) -> str:
    """Return a retrieval-optimized query; falls back to original on failure."""
    settings = get_settings()
    base = _trim_query(query)
    if not base:
        return query

    enabled = settings.query_rewrite_enabled if query_rewrite_enabled is None else query_rewrite_enabled
    if not enabled:
        return _augment_without_llm(base, profile, page_title=page_title, page_slug=page_slug)

    cache_key = _cache_key(base, session_id)
    cached = _SESSION_CACHE.get(cache_key)
    if cached:
        return cached

    rewritten = base
    if llm_text_fn is not None:
        try:
            prompt = _REWRITE_PROMPT.format(query=base)
            raw = llm_text_fn([{"role": "user", "content": prompt}], max_tokens=80)
            candidate = _trim_query(str(raw or ""))
            if candidate and len(candidate) >= 8:
                rewritten = candidate
        except Exception as exc:
            logger.debug("query rewrite skipped: %s", exc)

    rewritten = _augment_without_llm(
        rewritten, profile, page_title=page_title, page_slug=page_slug
    )
    if len(_SESSION_CACHE) >= _MAX_CACHE:
        _SESSION_CACHE.clear()
    _SESSION_CACHE[cache_key] = rewritten
    return rewritten


def _augment_without_llm(
    query: str,
    profile: dict[str, Any] | None,
    *,
    page_title: str = "",
    page_slug: str = "",
) -> str:
    parts = [query]
    if profile and profile.get("project_need"):
        need = str(profile["project_need"]).strip()
        if need and need.lower() not in query.lower():
            parts.append(need)
    if page_title and page_title.lower() not in query.lower():
        parts.append(page_title)
    if page_slug and page_slug.lower() not in query.lower():
        parts.append(page_slug)
    return " ".join(parts).strip()


def clear_rewrite_cache() -> None:
    _SESSION_CACHE.clear()
