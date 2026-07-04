from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.crawler.chunker import chunk_pages
from app.crawler.page_loader import dict_to_scraped_page

logger = logging.getLogger(__name__)

_seed_chunks: list[dict[str, Any]] | None = None


def _load_seed_chunks() -> list[dict[str, Any]]:
    global _seed_chunks
    if _seed_chunks is not None:
        return _seed_chunks

    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "data" / "formatted" / "pages_latest.json",
        root / "data" / "formatted" / "pages_seed.json",
    ):
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        pages = []
        for item in raw:
            sp = dict_to_scraped_page(item)
            if sp:
                pages.append(sp)
        if not pages:
            continue

        chunks = chunk_pages(pages, deduplicate=True)
        _seed_chunks = []
        for c in chunks:
            _seed_chunks.append(
                {
                    "chunk_id": c.chunk_id,
                    "page_id": c.page_id,
                    "source_url": c.source_url,
                    "page_title": c.page_title,
                    "page_category": c.page_category,
                    "content_type": c.content_type,
                    "chunk_text": c.chunk_text,
                    "citation_text": c.citation_text,
                    "score": 0.0,
                    "rerank_score": 0.0,
                }
            )
        logger.info("Loaded %s seed fallback chunks from %s", len(_seed_chunks), path.name)
        return _seed_chunks

    logger.warning("No seed pages found for fallback retrieval.")
    _seed_chunks = []
    return _seed_chunks


def _tokenize(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def seed_fallback_search(
    query: str,
    top_k: int = 8,
    page_category: str | None = None,
) -> list[dict[str, Any]]:
    """Keyword overlap search over bundled seed pages when vector DB is empty."""
    chunks = _load_seed_chunks()
    if not chunks:
        return []

    q_tokens = _tokenize(query)
    if not q_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    for ch in chunks:
        if page_category and page_category != "general":
            if ch.get("page_category") != page_category:
                continue
        text_tokens = _tokenize(ch.get("chunk_text", "") + " " + ch.get("page_title", ""))
        if not text_tokens:
            continue
        overlap = len(q_tokens & text_tokens) / max(len(q_tokens), 1)
        if overlap <= 0:
            continue
        item = dict(ch)
        item["score"] = overlap
        item["rerank_score"] = overlap
        scored.append((overlap, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in scored[:top_k]]
