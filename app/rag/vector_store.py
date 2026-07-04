from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.crawler.schema import PageChunk

logger = logging.getLogger(__name__)


class VectorStore(ABC):
    @abstractmethod
    def add_chunks(
        self, chunks: list[PageChunk], embeddings: list[list[float]]
    ) -> None: ...

    @abstractmethod
    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    @abstractmethod
    def count(self) -> int: ...

    def get_by_metadata(
        self,
        filter_metadata: dict[str, Any],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        return []

    def reset_collection(self) -> None:
        """Drop and recreate the backing collection."""

    def get_all_content_hashes(self) -> set[str]:
        return set()


class ChromaVectorStore(VectorStore):
    def __init__(self) -> None:
        import chromadb

        settings = get_settings()
        self.collection_name = settings.chroma_collection
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
        self._col = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"ChromaDB collection '{self.collection_name}' ready. Docs: {self._col.count()}"
        )

    def add_chunks(
        self, chunks: list[PageChunk], embeddings: list[list[float]]
    ) -> None:
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings length mismatch.")

        ids = [c.chunk_id for c in chunks]
        documents = [c.chunk_text for c in chunks]
        metadatas = [
            {
                "chunk_id": c.chunk_id,
                "page_id": c.page_id,
                "source_url": c.source_url,
                "page_title": c.page_title,
                "page_category": c.page_category,
                "source_tier": c.source_tier,
                "content_type": c.content_type,
                "section": c.section,
                "token_count": c.token_count,
                "citation_text": c.citation_text,
                "content_hash": c.content_hash,
                "source_freshness": c.metadata.source_freshness or "",
            }
            for c in chunks
        ]

        batch_size = 500
        for i in range(0, len(ids), batch_size):
            self._col.upsert(
                ids=ids[i : i + batch_size],
                embeddings=embeddings[i : i + batch_size],
                documents=documents[i : i + batch_size],
                metadatas=metadatas[i : i + batch_size],
            )
        logger.info(f"Upserted {len(ids)} chunks into ChromaDB.")

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        total = self._col.count()
        if total == 0:
            return []

        effective_k = min(top_k, total)
        results = self._col.query(
            query_embeddings=[query_embedding],
            n_results=effective_k,
            where=filter_metadata,
            include=["documents", "metadatas", "distances"],
        )

        output: list[dict[str, Any]] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            output.append({"chunk_text": doc, "score": 1.0 - dist, **meta})
        return output

    def count(self) -> int:
        return self._col.count()

    def get_by_metadata(
        self,
        filter_metadata: dict[str, Any],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not filter_metadata or self._col.count() == 0:
            return []
        try:
            results = self._col.get(
                where=filter_metadata,
                limit=limit,
                include=["documents", "metadatas"],
            )
        except Exception as exc:
            logger.debug("Chroma get_by_metadata failed: %s", exc)
            return []
        output: list[dict[str, Any]] = []
        docs = results.get("documents") or []
        metas = results.get("metadatas") or []
        for doc, meta in zip(docs, metas):
            if not meta:
                continue
            output.append({"chunk_text": doc, "score": 1.0, **meta})
        return output

    def reset_collection(self) -> None:
        settings = get_settings()
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._col = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Reset Chroma collection '%s'", self.collection_name)

    def get_all_content_hashes(self) -> set[str]:
        try:
            total = self._col.count()
            if total == 0:
                return set()
            result = self._col.get(include=["metadatas"])
            return {
                m.get("content_hash", "")
                for m in (result.get("metadatas") or [])
                if m.get("content_hash")
            }
        except Exception as exc:
            logger.warning(f"get_all_content_hashes failed: {exc}")
            return set()


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = ChromaVectorStore()
    return _store


def reset_vector_store() -> VectorStore:
    """Clear cached store and reset the Chroma collection."""
    global _store
    _store = None
    settings = get_settings()
    chroma_path = Path(settings.chroma_persist_dir)
    if chroma_path.exists():
        shutil.rmtree(chroma_path)
        logger.info("Removed Chroma persist dir: %s", chroma_path)
    chroma_path.mkdir(parents=True, exist_ok=True)
    _store = ChromaVectorStore()
    return _store
