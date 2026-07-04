from __future__ import annotations

import logging
import re
from typing import Generator

import tiktoken

from app.config.settings import get_settings
from app.crawler.schema import ChunkMetadata, PageChunk, ScrapedPage

logger = logging.getLogger(__name__)

# Lazy-loaded to avoid network download at import time (cold-start risk on Cloud Run).
_TOKENIZER = None
_HEADING_RE = re.compile(
    r"^(#{1,4}\s+\S.{0,120}|[A-Z][A-Z\s\-:]{4,}[A-Z])$",
    re.MULTILINE,
)


def _count_tokens(text: str) -> int:
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = tiktoken.get_encoding("cl100k_base")
    return len(_TOKENIZER.encode(text))


def _split_into_blocks(text: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\n+", text.strip()) if b.strip()]


def _is_heading(block: str) -> bool:
    lines = block.strip().splitlines()
    if len(lines) != 1:
        return False
    return bool(_HEADING_RE.match(lines[0].strip()))


def _merge_headings_with_body(blocks: list[str]) -> list[str]:
    merged: list[str] = []
    i = 0
    while i < len(blocks):
        if _is_heading(blocks[i]) and i + 1 < len(blocks):
            merged.append(blocks[i] + "\n\n" + blocks[i + 1])
            i += 2
        else:
            merged.append(blocks[i])
            i += 1
    return merged


def _greedy_chunk(
    blocks: list[str],
    min_tokens: int,
    max_tokens: int,
    overlap_tokens: int,
) -> Generator[tuple[str, int], None, None]:
    current_parts: list[str] = []
    current_tokens: int = 0

    def _flush() -> Generator[tuple[str, int], None, None]:
        nonlocal current_parts, current_tokens
        if current_parts and current_tokens >= 30:
            yield "\n\n".join(current_parts), current_tokens
        if current_parts:
            last = current_parts[-1]
            last_tok = _count_tokens(last)
            current_parts = [last] if last_tok <= overlap_tokens else []
            current_tokens = last_tok if current_parts else 0
        else:
            current_parts = []
            current_tokens = 0

    for block in blocks:
        block_tokens = _count_tokens(block)
        if block_tokens > max_tokens:
            sentences = re.split(r"(?<=[.!?])\s+", block)
            for sent in sentences:
                sent_tokens = _count_tokens(sent)
                if current_tokens + sent_tokens > max_tokens and current_tokens >= min_tokens:
                    yield from _flush()
                current_parts.append(sent)
                current_tokens += sent_tokens
            continue

        if current_tokens + block_tokens > max_tokens and current_tokens >= min_tokens:
            yield from _flush()

        current_parts.append(block)
        current_tokens += block_tokens

    if current_parts and current_tokens >= 30:
        yield "\n\n".join(current_parts), current_tokens


def chunk_page(page: ScrapedPage, settings=None) -> list[PageChunk]:
    if settings is None:
        settings = get_settings()

    text = page.clean_text.strip()
    if not text:
        return []

    blocks = _merge_headings_with_body(_split_into_blocks(text))
    chunks: list[PageChunk] = []
    for idx, (chunk_text, token_count) in enumerate(
        _greedy_chunk(
            blocks,
            min_tokens=settings.chunk_min_tokens,
            max_tokens=settings.chunk_max_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
        )
    ):
        chunk = PageChunk(
            chunk_id=f"{page.id}_{idx:04d}",
            page_id=page.id,
            source_url=page.source_url,
            page_title=page.page_title,
            page_category=page.page_category,
            source_tier=page.source_tier,
            content_type=page.content_type,
            section=page.section,
            chunk_text=chunk_text,
            token_count=token_count,
            metadata=ChunkMetadata(
                source_freshness=page.metadata.source_freshness,
            ),
        )
        chunk.embedding_text = chunk.build_embedding_text()
        chunk.citation_text = chunk.build_citation_text()
        chunks.append(chunk)

    return chunks


def chunk_pages(pages: list[ScrapedPage], deduplicate: bool = True) -> list[PageChunk]:
    all_chunks: list[PageChunk] = []
    for page in pages:
        all_chunks.extend(chunk_page(page))

    if deduplicate:
        from app.crawler.deduplicator import deduplicate_chunks

        all_chunks = deduplicate_chunks(all_chunks)

    logger.info(f"chunk_pages: {len(all_chunks)} chunks from {len(pages)} pages.")
    return all_chunks
