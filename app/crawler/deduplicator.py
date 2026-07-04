from __future__ import annotations

import logging

from app.crawler.schema import PageChunk

logger = logging.getLogger(__name__)


def deduplicate_chunks(
    chunks: list[PageChunk],
    existing_hashes: set[str] | None = None,
) -> list[PageChunk]:
    seen: set[str] = set(existing_hashes or [])
    unique: list[PageChunk] = []
    dropped = 0

    for chunk in chunks:
        h = chunk.content_hash
        if not h:
            unique.append(chunk)
            continue
        if h in seen:
            dropped += 1
            continue
        seen.add(h)
        unique.append(chunk)

    if dropped:
        logger.info(
            f"Deduplication: kept {len(unique)}/{len(chunks)} chunks "
            f"({dropped} duplicates removed)."
        )
    return unique
