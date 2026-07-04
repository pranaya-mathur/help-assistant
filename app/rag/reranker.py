from __future__ import annotations

import logging
from typing import Any

import httpx

from app.agent.page_context import urls_match
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "services": ["service", "development", "mobile", "web", "cloud", "staffing"],
    "ai_agents": ["ai", "agent", "agentic", "llm", "genai", "machine learning", "automation"],
    "case_studies": ["case study", "portfolio", "client", "project", "success"],
    "about": ["about", "team", "company", "who we"],
    "contact": ["contact", "reach", "email", "call", "meeting"],
    "careers": ["career", "job", "hiring"],
    "blog": ["blog", "article", "insight"],
}

_cross_encoder_model: Any = None


def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int | None = None,
    page_url: str | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    k = top_k or settings.reranker_top_k
    if not chunks:
        return []

    backend = (settings.reranker_backend or "heuristic").strip().lower()
    try:
        if backend == "cohere" and settings.cohere_api_key:
            return _cohere_rerank(query, chunks, k, settings, page_url=page_url)
        if backend == "cross_encoder":
            return _cross_encoder_rerank(query, chunks, k, page_url=page_url)
    except Exception as exc:
        logger.warning("Reranker backend %s failed; using heuristic fallback: %s", backend, exc)

    return _heuristic_rerank(query, chunks, k, page_url=page_url)


def _diversify_by_source(
    ranked: list[dict[str, Any]], top_k: int, max_per_source: int = 2
) -> list[dict[str, Any]]:
    """Cap chunks per source_url so one page (e.g. a single case study) can't
    occupy every slot; backfills from the remainder if diversity would
    otherwise leave slots unfilled."""
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    leftover: list[dict[str, Any]] = []
    for item in ranked:
        if len(selected) >= top_k:
            break
        src = str(item.get("source_url") or "")
        if counts.get(src, 0) >= max_per_source:
            leftover.append(item)
            continue
        counts[src] = counts.get(src, 0) + 1
        selected.append(item)
    for item in leftover:
        if len(selected) >= top_k:
            break
        selected.append(item)
    return selected


def _page_url_boost(chunk: dict[str, Any], page_url: str | None) -> float:
    if not page_url or not get_settings().page_context_boost_enabled:
        return 0.0
    if urls_match(str(chunk.get("source_url") or ""), page_url):
        return 0.30
    return 0.0


def _heuristic_rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int,
    page_url: str | None = None,
) -> list[dict[str, Any]]:
    query_lower = query.lower()
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        item = dict(chunk)
        boost = 0.0
        category = item.get("page_category", "")
        for kw in CATEGORY_KEYWORDS.get(category, []):
            if kw in query_lower:
                boost += 0.12
                break

        chunk_text_lower = item.get("chunk_text", "").lower()
        query_words = set(query_lower.split())
        overlap = sum(1 for w in query_words if len(w) > 4 and w in chunk_text_lower)
        boost += min(overlap * 0.02, 0.10)
        source_url = str(item.get("source_url") or "").lower()
        if "case stud" in query_lower and "/case-stud" in source_url:
            boost += 0.20
        if "pric" in query_lower and "capabilities-overview" in source_url:
            boost += 0.18
        if category == "blog" and any(
            token in query_lower for token in ("case stud", "pric", "pricing", "cost")
        ):
            boost -= 0.15
        boost += _page_url_boost(item, page_url)
        tier = str(item.get("source_tier") or "core")
        if tier == "core":
            boost += 0.08
        elif tier == "seo_geo":
            boost -= 0.25
        elif tier == "utility":
            boost -= 0.05

        item["rerank_score"] = float(item.get("score", 0.0)) + boost
        ranked.append(item)

    ranked.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    return _diversify_by_source(ranked, top_k)


def _cohere_rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int,
    settings: Any,
    page_url: str | None = None,
) -> list[dict[str, Any]]:
    documents = [
        str(c.get("chunk_text") or c.get("citation_text") or c.get("embedding_text") or "")
        for c in chunks
    ]
    # Ask Cohere for more candidates than we need so _diversify_by_source has
    # room to swap in chunks from other sources instead of just truncating.
    payload = {
        "model": settings.cohere_rerank_model,
        "query": query,
        "documents": documents,
        "top_n": min(max(top_k * 3, top_k), len(documents)),
    }
    headers = {
        "Authorization": f"Bearer {settings.cohere_api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post("https://api.cohere.ai/v1/rerank", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    ranked: list[dict[str, Any]] = []
    for item in data.get("results", []):
        idx = int(item.get("index", 0))
        if idx < 0 or idx >= len(chunks):
            continue
        out = dict(chunks[idx])
        out["rerank_score"] = float(item.get("relevance_score", 0.0))
        ranked.append(out)
    if ranked:
        return _diversify_by_source(ranked, top_k)
    return _heuristic_rerank(query, chunks, top_k, page_url=page_url)


def _get_cross_encoder() -> Any:
    global _cross_encoder_model
    if _cross_encoder_model is None:
        from sentence_transformers import CrossEncoder

        settings = get_settings()
        _cross_encoder_model = CrossEncoder(settings.cross_encoder_model)
    return _cross_encoder_model


def _cross_encoder_rerank(
    query: str,
    chunks: list[dict[str, Any]],
    top_k: int,
    page_url: str | None = None,
) -> list[dict[str, Any]]:
    model = _get_cross_encoder()
    pairs = [
        (
            query,
            str(c.get("chunk_text") or c.get("citation_text") or c.get("embedding_text") or ""),
        )
        for c in chunks
    ]
    scores = model.predict(pairs)
    ranked: list[dict[str, Any]] = []
    for chunk, score in zip(chunks, scores):
        out = dict(chunk)
        out["rerank_score"] = float(score) + _page_url_boost(out, page_url)
        ranked.append(out)
    ranked.sort(key=lambda c: c.get("rerank_score", 0.0), reverse=True)
    return _diversify_by_source(ranked, top_k)
