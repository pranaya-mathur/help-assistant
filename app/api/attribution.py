"""Persist visitor attribution (IP, fingerprint) into session metadata."""
from __future__ import annotations

from typing import Any

from app.api.schemas import ChatRequest, VisitorMeta


def persist_client_ip(store: Any, session_id: str, client_ip: str) -> None:
    """Store first and last seen client IP on the session."""
    ip = (client_ip or "").strip()[:45]
    if not ip:
        return
    session = store.get(session_id)
    if not session:
        return
    updates: dict[str, Any] = {"last_client_ip": ip}
    if not session.metadata.get("first_client_ip"):
        updates["first_client_ip"] = ip
    store.patch_metadata(session_id, updates)


def persist_user_agent(store: Any, session_id: str, user_agent: str) -> None:
    """Store browser user-agent once per session (first non-empty value)."""
    ua = (user_agent or "").strip()[:512]
    if not ua:
        return
    session = store.get(session_id)
    if not session:
        return
    if session.metadata.get("user_agent"):
        return
    store.patch_metadata(session_id, {"user_agent": ua})


def persist_visitor_meta(store: Any, session_id: str, visitor_meta: VisitorMeta | None) -> None:
    """Store widget fingerprint in session metadata (first capture only)."""
    if not visitor_meta:
        return
    session = store.get(session_id)
    if not session:
        return
    if session.metadata.get("visitor_meta_captured"):
        return

    updates: dict[str, Any] = {"visitor_meta_captured": True}
    if visitor_meta.timezone:
        updates["visitor_timezone"] = visitor_meta.timezone[:64]
    if visitor_meta.language:
        updates["visitor_language"] = visitor_meta.language[:32]
    if visitor_meta.scroll_depth_pct:
        updates["visitor_scroll_depth_pct"] = visitor_meta.scroll_depth_pct

    utm_from_meta: dict[str, str] = {}
    for field in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"):
        val = getattr(visitor_meta, field, "") or ""
        if val:
            utm_from_meta[field] = val[:256]
    if utm_from_meta and not session.metadata.get("utm_params"):
        updates["utm_params"] = utm_from_meta

    if visitor_meta.referrer:
        ref = visitor_meta.referrer[:2000]
        updates["visitor_referrer"] = ref
        if not session.metadata.get("referrer"):
            updates["referrer"] = ref

    if len(updates) > 1:
        store.patch_metadata(session_id, updates)


def persist_visitor_meta_from_chat(store: Any, session_id: str, request: ChatRequest) -> None:
    """Persist fingerprint fields sent on chat requests."""
    persist_visitor_meta(store, session_id, request.visitor_meta)
