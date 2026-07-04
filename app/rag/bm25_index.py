from __future__ import annotations

import logging
import math
import os
import pickle
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.crawler.schema import PageChunk

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:\+[a-z0-9]+)?")


def tokenize(text: str) -> list[str]:
    """Tokenize text for lightweight lexical retrieval.

    The regex intentionally keeps short business/technical terms such as
    ai, rag, llm, api, gdpr, soc2, and hipaa.
    """
    return _TOKEN_RE.findall(text.lower())


@dataclass(slots=True)
class BM25Document:
    chunk_id: str
    chunk_text: str
    metadata: dict[str, Any]
    tokens: list[str]


class BM25Index:
    def __init__(
        self,
        documents: list[BM25Document],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.doc_count = len(documents)
        self.avg_doc_len = (
            sum(len(doc.tokens) for doc in documents) / self.doc_count
            if self.doc_count
            else 0.0
        )
        self.doc_freqs: dict[str, int] = {}
        self.term_freqs: list[dict[str, int]] = []
        for doc in documents:
            freqs: dict[str, int] = {}
            for token in doc.tokens:
                freqs[token] = freqs.get(token, 0) + 1
            self.term_freqs.append(freqs)
            for token in freqs:
                self.doc_freqs[token] = self.doc_freqs.get(token, 0) + 1

    @classmethod
    def from_chunks(cls, chunks: list[PageChunk]) -> BM25Index:
        documents: list[BM25Document] = []
        for chunk in chunks:
            text = chunk.embedding_text or chunk.chunk_text
            tokens = tokenize(text)
            if not tokens:
                continue
            metadata = {
                "chunk_id": chunk.chunk_id,
                "page_id": chunk.page_id,
                "source_url": chunk.source_url,
                "page_title": chunk.page_title,
                "page_category": chunk.page_category,
                "source_tier": chunk.source_tier,
                "content_type": chunk.content_type,
                "section": chunk.section,
                "token_count": chunk.token_count,
                "citation_text": chunk.citation_text,
                "content_hash": chunk.content_hash,
                "source_freshness": chunk.metadata.source_freshness or "",
            }
            documents.append(
                BM25Document(
                    chunk_id=chunk.chunk_id,
                    chunk_text=chunk.chunk_text,
                    metadata=metadata,
                    tokens=tokens,
                )
            )
        return cls(documents)

    def save(self, path: str | Path) -> None:
        # Write to a temp file in the same directory then atomically replace the
        # target — a crash/kill mid-write can never leave a truncated/corrupt
        # index at `path` (readers either see the old complete file or the new
        # complete file, never a partial one).
        index_path = Path(path)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=index_path.parent, prefix=f".{index_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(self, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, index_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    @classmethod
    def load(cls, path: str | Path) -> BM25Index:
        with Path(path).open("rb") as fh:
            index = pickle.load(fh)
        if not isinstance(index, cls):
            raise TypeError("BM25 index file did not contain a BM25Index.")
        return index

    def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        filter_metadata: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        if not self.documents:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored: list[tuple[float, int, BM25Document]] = []
        for idx, doc in enumerate(self.documents):
            if filter_metadata and not _metadata_matches(doc.metadata, filter_metadata):
                continue
            score = self._score(query_tokens, idx)
            if score > min_score:
                scored.append((score, idx, doc))

        scored.sort(key=lambda item: (-item[0], item[1]))
        output: list[dict[str, Any]] = []
        for rank, (score, _, doc) in enumerate(scored[:top_k], start=1):
            output.append(
                {
                    "chunk_text": doc.chunk_text,
                    "score": score,
                    "bm25_score": score,
                    "bm25_rank": rank,
                    **doc.metadata,
                }
            )
        return output

    def find_by_source_url(self, url: str, limit: int = 3) -> list[dict[str, Any]]:
        from app.agent.page_context import urls_match

        if not url or not self.documents:
            return []
        out: list[dict[str, Any]] = []
        for doc in self.documents:
            src = str(doc.metadata.get("source_url") or "")
            if not urls_match(src, url):
                continue
            out.append(
                {
                    "chunk_text": doc.chunk_text,
                    "score": 1.0,
                    **doc.metadata,
                }
            )
            if len(out) >= limit:
                break
        return out

    def _score(self, query_tokens: list[str], doc_idx: int) -> float:
        freqs = self.term_freqs[doc_idx]
        doc_len = len(self.documents[doc_idx].tokens)
        if doc_len == 0 or self.avg_doc_len == 0:
            return 0.0

        score = 0.0
        for token in query_tokens:
            tf = freqs.get(token, 0)
            if tf == 0:
                continue
            df = self.doc_freqs.get(token, 0)
            idf = math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))
            denom = tf + self.k1 * (1 - self.b + self.b * doc_len / self.avg_doc_len)
            score += idf * (tf * (self.k1 + 1)) / denom
        return score


def build_bm25_index(chunks: list[PageChunk], path: str | Path | None = None) -> BM25Index:
    settings = get_settings()
    index = BM25Index.from_chunks(chunks)
    index.save(path or settings.bm25_index_path)
    logger.info("Built BM25 index with %d documents.", index.doc_count)
    return index


_cached_index: BM25Index | None = None
_cached_path: str | None = None


def get_bm25_index(path: str | Path | None = None) -> BM25Index:
    global _cached_index, _cached_path
    settings = get_settings()
    index_path = str(path or settings.bm25_index_path)
    if _cached_index is not None and _cached_path == index_path:
        return _cached_index
    _cached_index = BM25Index.load(index_path)
    _cached_path = index_path
    logger.info("Loaded BM25 index from %s with %d documents.", index_path, _cached_index.doc_count)
    return _cached_index


def reset_bm25_index_cache() -> None:
    global _cached_index, _cached_path
    _cached_index = None
    _cached_path = None


def _metadata_matches(metadata: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, value in expected.items():
        if metadata.get(key) != value:
            return False
    return True
