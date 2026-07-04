from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

from openai import APIStatusError, OpenAI, RateLimitError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.agent.claim_validator import enforce_grounding
from app.agent.conversation_stage import compute_conversation_stage
from app.agent.grounding import get_grounding_provider
from app.agent.guardrails import run_guardrails, wrap_retrieved_chunk
from app.agent.intent_classifier import _is_booking_intent, classify_with_history
from app.agent.lead_intelligence import (
    compute_lead_scoring,
    is_frustrated,
    requests_human,
    should_offer_human_escalation,
)
from app.agent.lead_extractor import (
    count_user_turns,
    extract_from_message,
    extract_incremental,
    get_missing_lead_fields,
    has_contact_capture,
    is_lead_complete,
    merge_profiles,
    next_qualification_question,
    next_progressive_question,
    should_append_qualification,
)
from app.api.history import sanitize_conversation_history
from app.agent.prompts import (
    PROFILE_EXTRACT_PROMPT,
    format_answer_user_prompt,
    load_system_prompt,
)
from app.agent.state import AgentState, Citation
from app.agent.suggested_replies import build_suggested_replies, merge_suggested_replies
from app.agent.llm_suggested_replies import generate_llm_suggested_replies
from app.config.settings import get_settings
from app.crawler.page_loader import infer_page_category
from app.crawler.schema import ALLOWED_DOMAINS
from app.integrations.apollo import enrich_lead_hint_sync
from app.agent.retrieval_plan import resolve_retrieval_plan
from app.rag.reranker import rerank_chunks
from app.rag.retriever import get_retriever

logger = logging.getLogger(__name__)

SCOPE_REFUSAL_ANSWER = (
    "I'm focused on Mobcoder AI services and partnerships. "
    "Ask me about our AI, mobile, web, or engineering capabilities."
)


def _filter_retrieved_chunks(
    chunks: list[dict[str, Any]], plan: Any
) -> list[dict[str, Any]]:
    """Drop off-topic sources for portfolio/pricing queries."""
    topic = getattr(plan, "prompt_page_category", None) or getattr(plan, "page_category", None)
    if not chunks or not topic:
        return chunks

    if topic == "case_studies":
        filtered: list[dict[str, Any]] = []
        for c in chunks:
            url = str(c.get("source_url") or "").lower()
            if "/blog/" in url:
                continue
            if "/case-stud" in url or url.rstrip("/").endswith("mobcoder.ai/case-studies"):
                filtered.append(c)
        return filtered or chunks

    if topic == "pricing":
        filtered = [
            c
            for c in chunks
            if "/blog/" not in str(c.get("source_url") or "").lower()
        ]
        preferred = [
            c
            for c in filtered
            if "capabilities-overview" in str(c.get("source_url") or "").lower()
        ]
        if preferred:
            rest = [c for c in filtered if c not in preferred]
            return preferred + rest
        return filtered or chunks

    return chunks


def _should_refuse_without_rag(state: dict[str, Any]) -> bool:
    """Refuse only when intent is off-topic and retrieval found no MobCoder content."""
    if state.get("intent") != "out_of_scope":
        return False
    chunks = state.get("retrieved_chunks") or []
    page_chunks = state.get("current_page_chunks") or []
    return not chunks and not page_chunks


def _reconcile_intent_after_retrieval(state: dict[str, Any]) -> dict[str, Any]:
    """Retrieve-first: if MobCoder chunks exist, answer via RAG — not scope refusal."""
    if state.get("intent") != "out_of_scope":
        return state
    chunks = state.get("retrieved_chunks") or []
    page_chunks = state.get("current_page_chunks") or []
    if not chunks and not page_chunks:
        return state

    profile = state.get("lead_profile") or {}
    history = state.get("conversation_history")
    page_category = state.get("page_category") or "general"
    if page_category == "general" and chunks:
        page_category = str(chunks[0].get("page_category") or "general")
    stage = compute_conversation_stage("help", history, profile)
    scoring = compute_lead_scoring(
        profile,
        "help",
        history=history,
        page_url=str(state.get("page_url") or ""),
        user_query=str(state.get("user_query") or ""),
    )
    return {
        **state,
        "intent": "help",
        "page_category": page_category,
        "stage": stage,
        "lead_score": scoring["lead_score"],
        "lead_score_numeric": scoring["lead_score_numeric"],
        "meeting_readiness": scoring["meeting_readiness"],
    }
_RETRYABLE = (RateLimitError, APIStatusError)
_openai_client: OpenAI | None = None
LLM_FAILURE_MESSAGE = "Sorry, I had trouble generating a response. Please try again."
HUMAN_HANDOFF_TEMPLATE = (
    "Absolutely — I'll step aside so you can reach a real person on the MobCoder team.\n\n"
    "You can [contact the team directly]({contact_url}) and they'll follow up with you."
)
# Answers must never OPEN with what the bot doesn't know — rewrite that first
# sentence into a graceful pointer and let the verified facts that follow carry it.
_UNKNOWN_OPENER_RE = re.compile(
    r"^\s*(?:Unfortunately,?\s*)?I\s+(?:don'?t|do\s+not)\s+(?:currently\s+)?"
    r"(?:have|know)\b[^.!?\n]*[.!?]\s*(?:However,?\s*)?",
    re.I,
)
_UNKNOWN_OPENER_REPLACEMENT = (
    "That exact detail isn't published on mobcoder.ai — our team can share it directly. "
)
GROUNDING_CAVEAT_TEMPLATE = (
    "_Some details above could not be fully verified against mobcoder.ai — "
    "please [contact our team]({contact_url}) to confirm before relying on them._"
)

_GENERIC_QUERY_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|tell me more|more info|help)\b",
    re.I,
)


def _page_category_from_url(page_url: str) -> str:
    if not page_url or not page_url.strip():
        return "general"
    return infer_page_category(page_url.strip())


def _apply_page_category(state: dict[str, Any], page_category: str) -> str:
    """Prefer URL-derived category when classifier says general or query is generic."""
    query = (state.get("user_query") or "").strip()
    current = page_category or "general"
    url_cat = _page_category_from_url(str(state.get("page_url") or ""))
    if url_cat == "general":
        return current
    if current == "general" or (
        len(query) < 40 and _GENERIC_QUERY_RE.match(query)
    ):
        return url_cat
    return current


def _get_llm() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=get_settings().require_openai_key())
    return _openai_client


def _is_official_url(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False


def _citation_display_title(chunk: dict[str, Any]) -> str:
    title = (chunk.get("page_title") or "").strip()
    if title:
        return title
    url = (chunk.get("source_url") or "").strip()
    if not url:
        return "mobcoder.ai"
    try:
        path = urlparse(url).path.strip("/")
        if path:
            slug = path.split("/")[-1]
            return slug.replace("-", " ").title() or "mobcoder.ai"
    except Exception:
        pass
    return url


_SOURCES_BLOCK_RE = re.compile(r"\n+\*\*Sources\*\*\s*\n.*", re.I | re.S)
_SOURCES_LINE_RE = re.compile(r"^\s*\*\*Sources\*\*\s*$", re.I | re.M)
_BOOKING_CTA_RE = re.compile(
    r"\b(book|schedule|discovery call|calendly)\b",
    re.I,
)
_HOMEPAGE_URL_RE = re.compile(
    r"https?://(?:www\.)?mobcoder\.ai/?(?:[\"'\s\)]|$)",
    re.I,
)
# Matches a markdown link whose label mentions booking but whose URL is the contact page.
_CONTACT_BOOKING_LINK_RE = re.compile(
    r"\[([^\]]*(?:book|schedule|discovery call|30.minute)[^\]]*)\]"
    r"\(https?://(?:www\.)?mobcoder\.ai/contact-us[^)]*\)",
    re.I,
)


def _strip_sources_markdown(answer: str) -> str:
    """Remove inline Sources blocks — the widget renders citations separately."""
    text = _SOURCES_BLOCK_RE.sub("", answer)
    text = _SOURCES_LINE_RE.sub("", text)
    return text.rstrip()


def _dedupe_citations(citations: list[Citation]) -> list[Citation]:
    seen: set[str] = set()
    out: list[Citation] = []
    for c in citations:
        url = (c.get("source_url") or "").strip().rstrip("/").lower()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(c)
    return out


def _normalize_booking_links(answer: str, calendly_url: str) -> str:
    """Replace homepage/contact-page links used as booking CTAs with the real Calendly URL."""
    if not calendly_url or not _BOOKING_CTA_RE.search(answer):
        return answer
    answer = _HOMEPAGE_URL_RE.sub(f"{calendly_url} ", answer)
    # When the answer is booking-context, replace any markdown link to contact-us with Calendly.
    # The LLM sometimes writes [this link](contact-us) so we match by URL, not by label.
    answer = re.sub(
        r"\[([^\]]+)\]\(https?://(?:www\.)?mobcoder\.ai/contact-us[^)]*\)",
        lambda m: f"[{m.group(1)}]({calendly_url})",
        answer,
    )
    # A link pointing at Calendly must not be labeled "contact page".
    answer = re.sub(
        r"\[contact\s+page\]\((https?://calendly\.com[^)]*)\)",
        r"[booking page](\1)",
        answer,
        flags=re.I,
    )
    return answer


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _llm_json(prompt: str) -> dict[str, Any]:
    settings = get_settings()
    response = _get_llm().chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=300,
        response_format={"type": "json_object"},
    )
    raw = response.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


@retry(
    retry=retry_if_exception_type(_RETRYABLE),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
def _llm_text(messages: list[dict[str, str]], max_tokens: int = 600) -> str:
    settings = get_settings()
    response = _get_llm().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.3,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content or ""


def _llm_text_stream(messages: list[dict[str, str]], max_tokens: int = 600) -> Iterator[str]:
    settings = get_settings()
    stream = _get_llm().chat.completions.create(
        model=settings.openai_model,
        messages=messages,
        temperature=0.3,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


def _answer_user_prompt_from_state(state: dict[str, Any], context: str) -> str:
    settings = get_settings()
    profile = state.get("lead_profile", {})
    personalization = enrich_lead_hint_sync(profile)
    prompt = format_answer_user_prompt(
        intent=str(state.get("intent") or ""),
        stage=str(state.get("stage") or "discover"),
        page_category=str(state.get("page_category") or "general"),
        user_query=str(state.get("user_query") or ""),
        context=context,
        name=profile.get("name") or "not provided",
        email=profile.get("email") or "not provided",
        company=profile.get("company") or "not provided",
        project_need=profile.get("project_need") or "not provided",
        timeline=profile.get("timeline") or "not provided",
        budget_band=profile.get("budget_band") or "not provided",
        page_url=str(state.get("page_url") or "") or "not provided",
        page_title=str(state.get("page_title") or "") or "not provided",
        booking_cta=settings.booking_cta,
        contact_page_url=settings.contact_page_url,
        calendly_url=settings.calendly_url,
        personalization_line=personalization or "",
        current_page_chunks=list(state.get("current_page_chunks") or []),
        session_context=dict(state.get("session_context") or {}),
        frustrated=is_frustrated(str(state.get("user_query") or "")),
    )
    # For referential follow-ups, hard-instruct the LLM to stay on the prior topic.
    query = str(state.get("user_query") or "")
    history = state.get("conversation_history") or []
    if _REFERENTIAL_RE.match(query.strip()) and history:
        for turn in reversed(history):
            if turn.get("role") == "assistant":
                topic_hint = turn.get("content", "").split(".")[0].strip()[:150]
                if topic_hint:
                    prompt += (
                        f"\n\nIMPORTANT: The visitor is asking a follow-up specifically about: "
                        f'"{topic_hint}". Answer ONLY about that exact topic. '
                        "Do NOT pivot to other services, industries, or case studies."
                    )
                break
    return prompt


def _citation_snippet(chunk: dict[str, Any], max_len: int = 140) -> str:
    raw = (chunk.get("citation_text") or "").strip()
    if raw.lower().startswith("source:"):
        raw = raw.split(":", 1)[-1].strip()
    # Ingested citation_text is sometimes just crawler metadata with no real
    # excerpt (e.g. "| Category: contact | URL: https://..."). Fall back to
    # the chunk body rather than surface that placeholder to visitors.
    if not raw or re.match(r"^\|?\s*category\s*:", raw, re.I):
        raw = (chunk.get("chunk_text") or "").strip()
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 3].rstrip() + "..."


def _citation_from_chunk(chunk: dict[str, Any]) -> Citation:
    return Citation(
        chunk_id=chunk.get("chunk_id", ""),
        page_title=_citation_display_title(chunk),
        source_url=chunk.get("source_url", ""),
        citation_text=chunk.get("citation_text", ""),
        score=float(chunk.get("rerank_score", chunk.get("score", 0.0))),
        snippet=_citation_snippet(chunk),
        page_category=str(chunk.get("page_category") or ""),
    )


def _build_context_from_chunks(
    chunks: list[dict[str, Any]],
    *,
    current_page_chunks: list[dict[str, Any]] | None = None,
    start_index: int = 1,
) -> tuple[str, list[Citation], int]:
    context_parts: list[str] = []
    citations: list[Citation] = []
    idx = start_index

    for chunk in (current_page_chunks or [])[:2]:
        context_parts.append(
            f"[Current page — visitor is here now]\n{wrap_retrieved_chunk(chunk, idx)}"
        )
        citations.append(_citation_from_chunk(chunk))
        idx += 1

    current_ids = {str(c.get("chunk_id") or "") for c in (current_page_chunks or [])}
    for chunk in chunks:
        cid = str(chunk.get("chunk_id") or "")
        if cid and cid in current_ids:
            continue
        context_parts.append(wrap_retrieved_chunk(chunk, idx))
        citations.append(_citation_from_chunk(chunk))
        idx += 1

    return "\n\n---\n\n".join(context_parts), citations, idx


def build_generate_messages(state: dict[str, Any]) -> list[dict[str, str]] | None:
    """Build LLM messages for generate_answer; None if no generation needed."""
    if requests_human(str(state.get("user_query") or "")):
        return None
    if _should_refuse_without_rag(state):
        return None

    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return None

    context_text, _, _ = _build_context_from_chunks(
        chunks,
        current_page_chunks=list(state.get("current_page_chunks") or []),
    )

    user_prompt = _answer_user_prompt_from_state(state, context_text)

    history = sanitize_conversation_history(state.get("conversation_history"))[-6:]
    return [
        {"role": "system", "content": load_system_prompt()},
        *history,
        {"role": "user", "content": user_prompt},
    ]


_PRICING_QUERY_RE = re.compile(
    r"\b(pricing|price|cost|quote|budget|rates?|how much|engagement models?)\b",
    re.I,
)

# Queries that refer back to something already said — need prior context injected.
_REFERENTIAL_RE = re.compile(
    r"^(tell me more|more about|what about|can you|and the|the one|that one|"
    r"expand on|elaborate|explain that|go on|more detail|what else|"
    r"how about that|what did you mean|the above|that specific|"
    r"can you elaborate|could you|tell me about that)\b",
    re.I,
)


def _inject_prior_context(query: str, history: list[dict[str, str]] | None) -> str:
    """For short referential queries, prepend the first sentence of the last assistant turn."""
    q = query.strip()
    if not history or (not _REFERENTIAL_RE.match(q) and len(q.split()) > 8):
        return query
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            content = turn.get("content", "")
            first_sent = content.split(".")[0].strip()[:200]
            if first_sent and first_sent.lower() not in q.lower():
                return f"{q} {first_sent}"
            break
    return query


def _is_pricing_context(state: dict[str, Any]) -> bool:
    if str(state.get("page_category") or "") == "pricing":
        return True
    query = str(state.get("user_query") or "")
    return bool(_PRICING_QUERY_RE.search(query))


def _append_contact_quote_cta(answer: str, contact_url: str) -> str:
    if not contact_url or not answer:
        return answer
    normalized = answer.lower()
    if (
        contact_url in answer
        or "contact-us" in normalized
        or "contact us for a tailored quote" in normalized
    ):
        return answer
    label = "Contact us for a tailored quote"
    return f"{answer}\n\n**[{label} →]({contact_url})**"


def _apply_lead_scoring(state: dict[str, Any], profile: dict[str, Any], intent: str) -> dict[str, str | int]:
    scoring = compute_lead_scoring(
        profile,
        intent,
        history=state.get("conversation_history"),
        page_url=str(state.get("page_url") or ""),
        user_query=str(state.get("user_query") or ""),
    )
    return scoring


def classify_intent(state: dict[str, Any]) -> dict[str, Any]:
    query = state.get("user_query", "")
    history = state.get("conversation_history")
    profile = state.get("lead_profile", {})
    if not query.strip():
        return {
            **state,
            "intent": "general",
            "page_category": "general",
            "stage": "discover",
            "lead_score": "cold",
            "lead_score_numeric": 0,
            "meeting_readiness": "not_ready",
        }

    result = classify_with_history(
        query,
        history,
        llm_json_fn=_llm_json,
        prior_intent=str(state.get("prior_intent") or ""),
    )
    intent = result.intent
    page_category = _apply_page_category(state, result.page_category)
    stage = compute_conversation_stage(intent, history, profile)
    scoring = _apply_lead_scoring(state, profile, intent)
    return {
        **state,
        "intent": intent,
        "page_category": page_category,
        "stage": stage,
        "lead_score": scoring["lead_score"],
        "lead_score_numeric": scoring["lead_score_numeric"],
        "meeting_readiness": scoring["meeting_readiness"],
    }


def safety_check(state: dict[str, Any]) -> dict[str, Any]:
    query = state.get("user_query", "")
    is_safe, flags = run_guardrails(query)
    if not is_safe:
        if "off_topic" in flags:
            message = (
                "I'm focused on Mobcoder AI — our AI, software engineering, and partnership "
                "offerings. Ask me about services, case studies, your project, or booking a "
                "discovery call."
            )
        else:
            message = (
                "I can only help with questions about Mobcoder AI's services and how we might "
                "partner on your project. How can I assist with that?"
            )
        return {
            **state,
            "is_safe": False,
            "risk_flags": flags,
            "final_response": message,
        }
    return {**state, "is_safe": True, "risk_flags": flags}


def update_lead_profile(state: dict[str, Any]) -> dict[str, Any]:
    """Extract and merge lead fields only — never block the RAG answer path."""
    query = state.get("user_query", "")
    history = state.get("conversation_history")

    existing_profile = state.get("lead_profile", {})
    # Layer 3: run incremental passive extraction on every turn, then merge
    incremental_signals = extract_incremental(query, existing_profile)
    profile = merge_profiles(
        existing_profile,
        merge_profiles(extract_from_message(query), incremental_signals),
    )

    settings = get_settings()
    if settings.is_sales_layer_enabled() and settings.enable_lead_qualification:
        try:
            llm_data = _llm_json(
                PROFILE_EXTRACT_PROMPT.format(
                    user_query=query,
                    history=" | ".join(
                        m.get("content", "")
                        for m in (history or [])[-4:]
                        if m.get("role") == "user"
                    )
                    or "(none)",
                )
            )
            profile = merge_profiles(profile, llm_data)
        except Exception as exc:
            logger.debug(f"LLM profile extract skipped: {exc}")

    intent = state.get("intent", "general")
    missing = get_missing_lead_fields(profile, intent)
    stage = compute_conversation_stage(intent, history, profile)
    scoring = _apply_lead_scoring(state, profile, intent)

    return {
        **state,
        "lead_profile": profile,
        "missing_fields": missing,
        "needs_contact_info": False,
        "ready_for_booking": is_lead_complete(profile, intent),
        "stage": stage,
        "lead_score": scoring["lead_score"],
        "lead_score_numeric": scoring["lead_score_numeric"],
        "meeting_readiness": scoring["meeting_readiness"],
    }


def append_qualification(state: dict[str, Any]) -> dict[str, Any]:
    """After the grounded answer, add Calendly CTA and/or one soft qualify question."""
    settings = get_settings()
    intent = state.get("intent", "general")
    history = state.get("conversation_history")
    profile = state.get("lead_profile", {})
    answer = state.get("answer", "") or ""

    if settings.is_help_mode():
        query = str(state.get("user_query", "") or "")
        wants_human = requests_human(query)
        if (_is_booking_intent(query) or wants_human) and settings.calendly_url not in answer:
            answer += (
                f"\n\nYou can **schedule** a discovery call here: "
                f"**{settings.booking_cta}** — {settings.calendly_url}"
            )
        if not wants_human and is_frustrated(query) and "Prefer a human?" not in answer:
            answer += (
                f"\n\nPrefer a human? You can [reach the team directly]"
                f"({settings.contact_page_url}) any time."
            )

        contact_captured = has_contact_capture(profile) or is_lead_complete(profile, intent)
        if contact_captured and not bool(state.get("lead_acknowledged")):
            name = profile.get("name", "").strip()
            thanks = (
                f"Thanks{', ' + name if name else ''}! "
                "What else would you like to know about Mobcoder AI?"
            )
            if thanks.lower() not in answer.lower():
                answer += f"\n\n{thanks}"
            return {
                **state,
                "answer": answer,
                "needs_contact_info": False,
                "profile_question": "",
                "ready_for_booking": True,
                "mark_lead_acknowledged": True,
            }
        return {
            **state,
            "answer": answer,
            "needs_contact_info": False,
            "profile_question": "",
        }

    if not settings.enable_lead_qualification:
        if intent == "booking" and settings.calendly_url not in answer:
            answer += (
                f"\n\nYou can **schedule** a discovery call here: "
                f"**{settings.booking_cta}** — {settings.calendly_url}"
            )
        return {
            **state,
            "answer": answer,
            "needs_contact_info": False,
            "profile_question": "",
        }

    # meeting_readiness is computed per-turn from raw booking language (e.g. "let's
    # book a call") even when the intent classifier keeps the turn as "sales" —
    # treat that signal the same as a formal booking intent so the CTA doesn't lag.
    readiness = str(state.get("meeting_readiness") or "not_ready")

    # Booking intent: surface scheduling link once in the answer body
    if (intent == "booking" or readiness == "booking_requested") and settings.calendly_url not in answer:
        answer += (
            f"\n\nYou can **schedule** a discovery call here: "
            f"**{settings.booking_cta}** — {settings.calendly_url}"
        )

    contact_captured = has_contact_capture(profile) or is_lead_complete(profile, intent)
    if contact_captured:
        already_ack = bool(state.get("lead_acknowledged"))
        if not already_ack:
            name = profile.get("name", "").strip()
            thanks = f"Thanks{', ' + name if name else ''}! Our team will follow up shortly."
            if intent == "booking" and settings.calendly_url not in answer:
                answer += (
                    f"\n\n{thanks} You can **{settings.booking_cta}** here: {settings.calendly_url}"
                )
            else:
                answer += (
                    f"\n\n{thanks} What else would you like to know about Mobcoder AI?"
                )
            return {
                **state,
                "answer": answer,
                "needs_contact_info": False,
                "ready_for_booking": True,
                "profile_question": "",
                "mark_lead_acknowledged": True,
            }
        return {
            **state,
            "answer": answer,
            "needs_contact_info": False,
            "ready_for_booking": True,
            "profile_question": "",
        }

    stage = state.get("stage", "")
    if not should_append_qualification(intent, history, profile, stage=stage):
        return {
            **state,
            "answer": answer,
            "needs_contact_info": False,
            "profile_question": "",
        }

    missing = get_missing_lead_fields(profile, intent)

    if not missing:
        return {**state, "answer": answer, "needs_contact_info": False}

    # Layer 4: Use progressive contextual question instead of cold interrogation.
    # next_progressive_question picks a variant tuned to intent + project_type so
    # it reads as a natural continuation of the answer, not a form field.
    project_type = str(profile.get("project_type") or "")
    question = next_progressive_question(missing, intent, project_type=project_type)
    if not question:
        question = next_qualification_question(missing)

    # Layer 5 (CTA tiering): a lead already scoring "ready" (urgent timeline,
    # strong budget signal, etc. — see compute_lead_scoring) shouldn't be gated
    # behind the same single soft question a cold lead gets. Offer the direct
    # booking path alongside the question instead of making them fill more
    # fields first.
    if readiness == "ready" and settings.calendly_url not in answer:
        qualifier = (
            f"\n\n*{question}* Or if you'd rather just grab time directly: "
            f"**{settings.booking_cta}** — {settings.calendly_url}"
        )
    else:
        qualifier = f"\n\n*{question}*"
    if qualifier.strip() not in answer:
        answer += qualifier

    return {
        **state,
        "answer": answer,
        "needs_contact_info": True,
        "profile_question": question,
        "missing_fields": missing,
        "ready_for_booking": False,
    }


def retrieve_sources(state: dict[str, Any]) -> dict[str, Any]:
    query = state.get("user_query", "")
    profile = state.get("lead_profile", {})
    page_url = str(state.get("page_url") or "")
    page_title = str(state.get("page_title") or "")
    settings = get_settings()

    history = state.get("conversation_history") or []
    query_for_retrieval = _inject_prior_context(query, history)

    plan = resolve_retrieval_plan(
        user_query=query_for_retrieval,
        page_url=page_url,
        page_title=page_title,
        history=history,
    )
    augmented_query = plan.search_query
    if re.search(r"\b(pricing|price|cost|quote|budget|rates?)\b", query, re.I):
        augmented_query = (
            f"{augmented_query} flexible engagement models dedicated teams "
            "staff augmentation project-based pricing request a quote"
        )

    try:
        retriever = get_retriever()
        current_page_chunks: list[dict[str, Any]] = []
        if plan.include_current_page:
            current_page_chunks = retriever.fetch_chunks_for_page_url(page_url, limit=3)

        chunks = retriever.retrieve(
            query=augmented_query,
            top_k=settings.retrieval_candidate_k,
            page_category=plan.page_category,
            lead_profile=profile,
            page_url=page_url if plan.include_current_page else None,
            exclude_source_tiers=list(plan.exclude_source_tiers),
        )

        reranked = rerank_chunks(
            augmented_query,
            chunks,
            top_k=settings.reranker_top_k,
            page_url=page_url if plan.include_current_page else None,
        )
        reranked = _filter_retrieved_chunks(reranked, plan)
        prompt_category = plan.prompt_page_category or plan.page_category or state.get(
            "page_category", "general"
        )
        return _reconcile_intent_after_retrieval({
            **state,
            "retrieved_chunks": reranked,
            "current_page_chunks": current_page_chunks,
            "page_category": prompt_category,
            "retrieval_plan": {
                "search_query": plan.search_query,
                "page_category": plan.page_category,
                "prompt_page_category": plan.prompt_page_category,
                "exclude_source_tiers": list(plan.exclude_source_tiers),
                "include_current_page": plan.include_current_page,
            },
        })
    except Exception as exc:
        logger.error(f"retrieve_sources error: {exc}", exc_info=True)
        return _reconcile_intent_after_retrieval(
            {**state, "retrieved_chunks": [], "current_page_chunks": [], "error": str(exc)}
        )


def generate_answer(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if requests_human(str(state.get("user_query") or "")):
        return {
            **state,
            "answer": HUMAN_HANDOFF_TEMPLATE.format(contact_url=settings.contact_page_url),
            "citations": [],
        }
    if _should_refuse_without_rag(state):
        return {
            **state,
            "answer": SCOPE_REFUSAL_ANSWER,
            "citations": [],
        }

    chunks = state.get("retrieved_chunks", [])
    if not chunks:
        return {
            **state,
            "answer": (
                "I don't have enough information from mobcoder.ai to answer that confidently. "
                f"Please [contact our team]({settings.contact_page_url}) for a tailored quote."
            ),
            "citations": [],
        }

    context_text, citations, _ = _build_context_from_chunks(
        chunks,
        current_page_chunks=list(state.get("current_page_chunks") or []),
    )

    user_prompt = _answer_user_prompt_from_state(state, context_text)

    history = sanitize_conversation_history(state.get("conversation_history"))[-6:]
    messages: list[dict[str, str]] = [
        {"role": "system", "content": load_system_prompt()},
        *history,
        {"role": "user", "content": user_prompt},
    ]

    try:
        answer = _llm_text(messages, max_tokens=600)
        return {**state, "answer": answer, "citations": citations}
    except Exception as exc:
        logger.error(f"generate_answer error: {exc}")
        return {**state, "answer": LLM_FAILURE_MESSAGE, "citations": []}


def validate_grounding(state: dict[str, Any]) -> dict[str, Any]:
    answer = state.get("answer", "")
    chunks = state.get("retrieved_chunks", [])
    if not answer or not chunks:
        return {**state, "grounding_passed": True, "grounding_rewritten": False}
    # Deterministic human-handoff replies make no factual claims — skip grounding
    # so the caveat footer never lands on an escalation message.
    if requests_human(str(state.get("user_query") or "")):
        return {**state, "grounding_passed": True, "grounding_rewritten": False}

    settings = get_settings()
    provider = get_grounding_provider()
    llm_fn = _llm_json if settings.enable_llm_grounding else None
    fixed, result = provider.validate(answer, chunks, llm_json_fn=llm_fn)
    replace_answer = settings.grounding_replace_on_failure and not settings.is_help_mode()
    final_answer = fixed if replace_answer else answer
    if not result.is_grounded and not replace_answer and settings.is_help_mode():
        caveat = GROUNDING_CAVEAT_TEMPLATE.format(contact_url=settings.contact_page_url)
        if caveat.lower() not in final_answer.lower():
            final_answer += f"\n\n{caveat}"
    return {
        **state,
        "answer": final_answer,
        "grounding_passed": result.is_grounded,
        "grounding_rewritten": result.rewritten and replace_answer,
        "unsupported_claims": result.unsupported_claims,
    }


def _has_mobcoder_citation(answer: str, citations: list[Citation]) -> bool:
    text = answer.lower()
    if "mobcoder.ai" in text:
        return True
    for c in citations:
        url = (c.get("source_url") or "").lower()
        if "mobcoder.ai" in url:
            return True
    return False


def validate_citations(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    answer = state.get("answer", "")
    if not answer:
        return state

    for url in re.findall(r"https?://[^\s)\]\"'>]+", answer):
        if not _is_official_url(url):
            answer = answer.replace(url, settings.contact_page_url)

    # Human-handoff replies deliberately link the contact page — leave them alone.
    if not requests_human(str(state.get("user_query") or "")):
        answer = _normalize_booking_links(answer, settings.calendly_url)
    answer = _strip_sources_markdown(answer)

    return {**state, "answer": answer}


def build_final_response(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    answer = state.get("answer") or "How can I help you learn more about Mobcoder AI?"
    answer = _strip_sources_markdown(answer)
    if not requests_human(str(state.get("user_query") or "")):
        answer = _normalize_booking_links(answer, settings.calendly_url)
    # Kill the LLM's stock closer phrase — it makes consecutive answers read canned.
    answer = re.sub(r"features?\s+or\s+functionalit\w+", "features", answer, flags=re.I)
    rewritten = _UNKNOWN_OPENER_RE.sub(_UNKNOWN_OPENER_REPLACEMENT, answer, count=1)
    if rewritten != answer:
        idx = len(_UNKNOWN_OPENER_REPLACEMENT)
        rewritten = rewritten[:idx] + rewritten[idx : idx + 1].upper() + rewritten[idx + 1 :]
    answer = rewritten
    if (
        _is_pricing_context(state)
        and not settings.is_help_mode()
        and settings.is_sales_layer_enabled()
    ):
        answer = _append_contact_quote_cta(answer, settings.contact_page_url)

    citations = _dedupe_citations(list(state.get("citations", [])))
    profile = state.get("lead_profile") or {}
    intent = str(state.get("intent", "general"))
    stage = str(state.get("stage", "discover"))
    heuristic = build_suggested_replies(
        intent=intent,
        stage=stage,
        page_category=state.get("page_category", "general"),
        profile_question=state.get("profile_question", ""),
        needs_contact_info=bool(state.get("needs_contact_info", False)),
        lead_profile=profile,
        citations=citations,
        show_human_escalation=should_offer_human_escalation(state),
        user_query=str(state.get("user_query") or ""),
        lead_acknowledged=bool(state.get("lead_acknowledged")),
        page_url=str(state.get("page_url") or ""),
        page_title=str(state.get("page_title") or ""),
        session_context=dict(state.get("session_context") or {}),
    )
    llm_chips: list[str] = []
    if settings.llm_suggested_replies_enabled and intent != "out_of_scope":
        llm_chips = generate_llm_suggested_replies(
            user_query=str(state.get("user_query") or ""),
            answer=answer,
            page_category=str(state.get("page_category") or "general"),
            page_title=str(state.get("page_title") or ""),
            intent=intent,
            stage=stage,
            citations=citations,
            lead_profile=profile,
            llm_json_fn=_llm_json,
        )
    suggestions = merge_suggested_replies(heuristic, llm_chips)

    return {
        **state,
        "final_response": answer,
        "citations": citations,
        "suggested_replies": suggestions,
        "show_human_escalation": should_offer_human_escalation(state),
    }
