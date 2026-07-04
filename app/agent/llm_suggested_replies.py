from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app.config.settings import get_settings

logger = logging.getLogger(__name__)

_MAX_LLM_CHIPS = 3
_MAX_CHIP_LEN = 72

_CHIP_PROMPT = """You suggest short follow-up tap-to-send chips for a Mobcoder AI website chatbot visitor.

Rules:
- Return JSON only: {{"chips": ["...", "...", "..."]}}
- Up to 3 chips, each under 72 characters, phrased as what the VISITOR would type
- Chips must relate to the bot answer and page context below
- Do NOT invent pricing numbers or guarantees
- Include a discovery-call chip only when intent is sales/booking or stage is convert
- No duplicate or near-duplicate chips

Visitor question: {user_query}
Page category: {page_category}
Page title: {page_title}
Intent: {intent}
Stage: {stage}
Bot answer: {answer}
Citation titles: {citation_titles}
"""


def generate_llm_suggested_replies(
    *,
    user_query: str,
    answer: str,
    page_category: str,
    page_title: str,
    intent: str,
    stage: str,
    citations: list[dict[str, Any]] | None = None,
    lead_profile: dict[str, Any] | None = None,
    llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
) -> list[str]:
    """One small JSON LLM call; return 0–3 tap-to-send chips."""
    settings = get_settings()
    if not settings.llm_suggested_replies_enabled:
        return []
    if not answer or not (answer or "").strip():
        return []
    if intent == "out_of_scope":
        return []
    if llm_json_fn is None:
        return []

    titles = [
        (c.get("page_title") or "").strip()
        for c in (citations or [])
        if (c.get("page_title") or "").strip()
    ][:3]
    prompt = _CHIP_PROMPT.format(
        user_query=(user_query or "")[:500],
        page_category=page_category or "general",
        page_title=(page_title or "not provided")[:200],
        intent=intent or "general",
        stage=stage or "discover",
        answer=(answer or "")[:1200],
        citation_titles=", ".join(titles) or "none",
    )
    try:
        data = llm_json_fn(prompt)
        raw = data.get("chips") if isinstance(data, dict) else []
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                continue
            t = item.strip()
            if not t or len(t) > _MAX_CHIP_LEN:
                continue
            if t.lower() in {x.lower() for x in out}:
                continue
            out.append(t)
            if len(out) >= _MAX_LLM_CHIPS:
                break
        return out
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        logger.debug("llm_suggested_replies parse failed: %s", exc)
        return []
    except Exception as exc:
        logger.warning("llm_suggested_replies failed: %s", exc)
        return []
