from __future__ import annotations

import logging
import threading
from typing import Any

from app.agent.lead_extractor import extract_from_message, extract_incremental, merge_profiles
from app.agent.nodes import classify_intent, update_lead_profile
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
    return enriched


def dispatch_post_response_enrichment(
    *,
    state: dict[str, Any],
    session_id: str,
) -> None:
    """Fire intent/profile enrichment in a background thread."""

    def _run() -> None:
        try:
            enriched = enrich_state(dict(state))
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
        except Exception as exc:
            logger.warning("post_response enrichment failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="post-response-enrich").start()
