from __future__ import annotations

import logging
import threading
from typing import Any

from app.agent.lead_extractor import extract_from_message, extract_incremental, merge_profiles
from app.agent.nodes import classify_intent, update_lead_profile
from app.agent.routing import decide_routing
from app.config.settings import get_settings
from app.sessions.store import get_session_store

logger = logging.getLogger(__name__)


def enrich_state(state: dict[str, Any]) -> dict[str, Any]:
    """Post-response enrichment: intent + profile (async, not on hot path)."""
    from app.agent.nodes import SCOPE_REFUSAL_ANSWER, _should_refuse_without_rag, build_final_response

    settings = get_settings()
    query = str(state.get("user_query") or "")
    profile = merge_profiles(
        dict(state.get("lead_profile") or {}),
        merge_profiles(
            extract_from_message(query),
            extract_incremental(query, dict(state.get("lead_profile") or {})),
        ),
    )
    base = {**state, "lead_profile": profile}

    if settings.is_help_mode():
        enriched = classify_intent(base)
        if enriched.get("intent") != "out_of_scope":
            enriched["intent"] = "help"
        enriched = update_lead_profile(enriched)
    elif settings.is_sales_layer_enabled() and settings.enable_lead_qualification:
        enriched = update_lead_profile(classify_intent(base))
    else:
        enriched = classify_intent(base)
        enriched = update_lead_profile(enriched)

    if _should_refuse_without_rag(enriched):
        return build_final_response({
            **enriched,
            "answer": SCOPE_REFUSAL_ANSWER,
            "citations": [],
        })
    # Routing was decided inside the graph before intent/profile enrichment —
    # recompute it on the enriched state so responses carry the right CTA.
    return {**enriched, **decide_routing(enriched)}


def _notify_human_review_once(
    store: Any,
    session_id: str,
    state: dict[str, Any],
) -> None:
    """Send at most one Google Chat alert per session — the flag recurs every
    turn once a lead goes hot, but sales only needs to hear about it once."""
    from app.integrations.google_chat import notify_human_review

    session = store.get(session_id)
    metadata = dict(session.metadata or {}) if session else {}
    if metadata.get("human_review_notified"):
        return
    sent = notify_human_review(
        str(state.get("human_review_summary") or ""),
        session_id=session_id,
        qualification_score=dict(state.get("qualification_score") or {}),
        page_url=str(state.get("page_url") or ""),
    )
    if sent:
        store.patch_metadata(session_id, {"human_review_notified": True})


def persist_qualification(state: dict[str, Any], session_id: str) -> None:
    """Patch qualification/routing fields into session metadata and fire the
    human-review alert. `state` must already carry enriched scoring + routing
    (i.e. be the output of enrich_state)."""
    store = get_session_store()
    store.patch_metadata(
        session_id,
        {
            "qualification_score": state.get("qualification_score") or {},
            "cta_type": state.get("cta_type", ""),
            "needs_human_review": bool(state.get("needs_human_review")),
            "human_review_summary": state.get("human_review_summary", ""),
        },
    )
    # Outbound surfaces carry PII (name/email) — both require lead_consent,
    # same as the CRM dispatch. Scoring metadata above is consent-free.
    if state.get("needs_human_review") and state.get("lead_consent"):
        _notify_human_review_once(store, session_id, state)
    _log_lead_to_sheet(state, session_id)


def _log_lead_to_sheet(state: dict[str, Any], session_id: str) -> None:
    """Upsert a row in the lead-log Google Sheet for identified leads only —
    the sheet is a lead record, not traffic analytics, so anonymous sessions
    stay out of it. Re-sent every turn; the Apps Script upserts by session_id
    so the row tracks the latest score."""
    from app.integrations.google_sheets import log_lead_row

    profile = dict(state.get("lead_profile") or {})
    if not profile.get("email"):
        return
    if not state.get("lead_consent"):
        logger.debug("Sheet lead log skipped (no lead_consent) session=%s", session_id)
        return
    log_lead_row(state, session_id)


def dispatch_qualification_persistence(
    *,
    state: dict[str, Any],
    session_id: str,
) -> None:
    """Persist qualification + alert in a background thread. For the non-stream
    path, whose state is already enriched inside run_agent — no re-enrichment."""

    def _run() -> None:
        try:
            persist_qualification(dict(state), session_id)
        except Exception as exc:
            logger.warning("qualification persistence failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="qualification-persist").start()


def dispatch_post_response_enrichment(
    *,
    state: dict[str, Any],
    session_id: str,
) -> None:
    """Fire intent/profile enrichment in a background thread."""

    def _run() -> None:
        try:
            enriched = enrich_state(dict(state))  # includes recomputed routing
            store = get_session_store()
            store.patch_metadata(
                session_id,
                {
                    "last_intent": enriched.get("intent", ""),
                    "lead_profile": enriched.get("lead_profile") or {},
                    "lead_score": enriched.get("lead_score", ""),
                    "stage": enriched.get("stage", ""),
                },
            )
            persist_qualification(enriched, session_id)
        except Exception as exc:
            logger.warning("post_response enrichment failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="post-response-enrich").start()
