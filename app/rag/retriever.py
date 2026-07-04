from __future__ import annotations

import logging
import math
import time
from typing import Any

from app.agent.page_context import normalize_page_url
from app.config.settings import get_settings
from app.crawler.deduplicator import deduplicate_chunks
from app.crawler.schema import PageChunk
from app.rag.bm25_index import get_bm25_index
from app.rag.embeddings import get_embedding_client
from app.rag.fusion import reciprocal_rank_fusion
from app.rag.seed_fallback import seed_fallback_search
from app.rag.vector_store import get_vector_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MMR (Maximal Marginal Relevance)
# ---------------------------------------------------------------------------
# MMR selects a diverse subset from the initial candidate pool by iteratively
# picking the chunk that maximises relevance to the query while penalising
# similarity to already-selected chunks.
#
#   score = λ · sim(chunk, query) - (1-λ) · max_sim(chunk, selected)
#
# λ=1.0 → pure relevance (original ranking); λ=0.0 → pure diversity.
# Default λ=0.6 keeps strong relevance while removing near-duplicate chunks.
# ---------------------------------------------------------------------------

_MMR_LAMBDA = 0.6  # tunable via env in future; good default for a small corpus


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two pre-normalised or raw float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _mmr(
    query_vec: list[float],
    candidates: list[dict[str, Any]],
    top_k: int,
    lam: float = _MMR_LAMBDA,
) -> list[dict[str, Any]]:
    """Apply MMR to *candidates* and return *top_k* diverse chunks.

    Candidates must have an ``embedding`` key (list[float]).  Chunks without
    embeddings fall back gracefully to their Chroma cosine distance score.
    If fewer than top_k candidates are available they are all returned.
    """
    if len(candidates) <= top_k:
        return candidates

    # Separate chunks that carry their embedding vector from those that don't.
    # Chroma's query API does not return embeddings by default, so we use the
    # precomputed cosine score as a proxy for query similarity when the vector
    # is absent.
    selected: list[dict[str, Any]] = []
    remaining = list(candidates)

    while len(selected) < top_k and remaining:
        best_chunk: dict[str, Any] | None = None
        best_score = -float("inf")

        for chunk in remaining:
            query_sim = float(chunk.get("score", 0.0))  # Chroma 1-distance score

            if selected:
                emb = chunk.get("embedding")
                if emb:
                    max_sel_sim = max(
                        _cosine(emb, s["embedding"])
                        for s in selected
                        if s.get("embedding")
                    ) if any(s.get("embedding") for s in selected) else 0.0
                else:
                    # No embedding available — use source_url as a proxy:
                    # chunks from the same page are treated as maximally similar.
                    same_url = any(
                        s.get("source_url") == chunk.get("source_url")
                        for s in selected
                    )
                    max_sel_sim = 0.95 if same_url else 0.0
                mmr_score = lam * query_sim - (1 - lam) * max_sel_sim
            else:
                mmr_score = query_sim

            if mmr_score > best_score:
                best_score = mmr_score
                best_chunk = chunk

        if best_chunk is not None:
            selected.append(best_chunk)
            remaining.remove(best_chunk)

    return selected


# ---------------------------------------------------------------------------
# Query reformulation helpers
# ---------------------------------------------------------------------------

def _broaden_query(query: str, profile: dict[str, Any] | None = None) -> list[str]:
    """Return a list of progressively-broadened fallback queries.

    Called when the primary retrieve returns zero chunks so we don't fall
    straight to the canned "I don't have enough information" message.
    """
    variants: list[str] = []

    # Variant 1: strip project_need augmentation — query only
    base = query.strip()
    if profile and profile.get("project_need"):
        need = str(profile["project_need"]).strip()
        base_stripped = query.replace(need, "").strip()
        if base_stripped and base_stripped != base:
            variants.append(base_stripped)

    # Variant 2: first 6 words (removes long-tail noise)
    words = base.split()
    if len(words) > 6:
        variants.append(" ".join(words[:6]))

    # Variant 3: bare noun phrases — keep only alpha tokens > 4 chars
    keywords = " ".join(w for w in words if w.isalpha() and len(w) > 4)
    if keywords and keywords != base:
        variants.append(keywords)

    # Deduplicate while preserving order
    seen: set[str] = {base}
    return [v for v in variants if v not in seen and (seen.add(v) or True)]  # type: ignore[func-returns-value]


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------

class Retriever:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._embedder: Any = None
        self.store = get_vector_store()

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = get_embedding_client()
        return self._embedder

    def fetch_chunks_for_page_url(self, page_url: str, limit: int = 3) -> list[dict[str, Any]]:
        """Return chunks indexed for the exact page URL (normalized)."""
        if not page_url or not self.settings.page_context_boost_enabled:
            return []

        normalized = normalize_page_url(page_url)
        if not normalized:
            return []

        variants = list(
            dict.fromkeys(
                [
                    normalized,
                    normalized + "/",
                    normalized.rstrip("/"),
                    page_url.strip().rstrip("/"),
                ]
            )
        )

        seen_ids: set[str] = set()
        out: list[dict[str, Any]] = []

        if self.store.count() > 0:
            for variant in variants:
                if not variant or len(out) >= limit:
                    break
                for chunk in self.store.get_by_metadata(
                    {"source_url": variant},
                    limit=limit,
                ):
                    cid = str(chunk.get("chunk_id") or "")
                    if cid and cid in seen_ids:
                        continue
                    if cid:
                        seen_ids.add(cid)
                    chunk = dict(chunk)
                    chunk["page_context_prefetch"] = True
                    out.append(chunk)
                    if len(out) >= limit:
                        break

        if len(out) < limit:
            try:
                bm25_index = get_bm25_index(self.settings.bm25_index_path)
                for chunk in bm25_index.find_by_source_url(page_url, limit=limit):
                    cid = str(chunk.get("chunk_id") or "")
                    if cid and cid in seen_ids:
                        continue
                    if cid:
                        seen_ids.add(cid)
                    chunk = dict(chunk)
                    chunk["page_context_prefetch"] = True
                    out.append(chunk)
                    if len(out) >= limit:
                        break
            except Exception as exc:
                logger.debug("BM25 page URL lookup failed: %s", exc)

        if not out:
            logger.debug("No chunks found for page_url=%s", normalized)
        return out[:limit]

    def _bm25_has_documents(self) -> bool:
        """True if hybrid retrieval is enabled and the BM25 index has documents —
        used to decide whether a Chroma-empty condition should still attempt
        retrieval (BM25-only, via normal fusion with 0 vector candidates) instead
        of jumping straight to the tiny bundled seed-knowledge fallback."""
        if not self.settings.hybrid_retrieval_enabled:
            return False
        try:
            return get_bm25_index(self.settings.bm25_index_path).doc_count > 0
        except Exception:
            return False

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        page_category: str | None = None,
        lead_profile: dict[str, Any] | None = None,
        page_url: str | None = None,
        exclude_source_tiers: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve and diversify chunks for *query*.

        Strategy:
        1. Try category-filtered similarity search.
        2. If empty, retry without category filter.
        3. If still empty, attempt query reformulation (broadened variants).
        4. If still empty, fall back to bundled seed knowledge.
        5. Apply MMR on the candidate pool before returning top_k chunks.
        """
        k = top_k or self.settings.retrieval_top_k
        candidate_k = (
            self.settings.vector_top_k
            if self.settings.hybrid_retrieval_enabled
            else self.settings.retrieval_candidate_k
        )

        if self.store.count() == 0 and not self._bm25_has_documents():
            # Chroma empty AND (hybrid off, or BM25 also empty/unavailable) — only
            # then is the tiny bundled seed corpus really the best we can do.
            # Previously this short-circuited on Chroma alone, so a partial wipe
            # that left an intact, populated BM25 index still degraded straight
            # to the ~10-page seed fallback instead of using it.
            logger.info("Vector store empty — using bundled seed knowledge fallback.")
            results = seed_fallback_search(query, top_k=k, page_category=page_category)
            if results:
                return results
            if page_category and page_category != "general":
                return seed_fallback_search(query, top_k=k, page_category=None)
            return []

        query_vec = self.embedder.embed_text(query)
        filter_meta = None
        if page_category and page_category != "general":
            filter_meta = {"page_category": page_category}

        chunks = self._retrieve_candidates(
            query=query,
            query_vec=query_vec,
            vector_top_k=candidate_k,
            final_top_k=max(k, self.settings.hybrid_final_top_k),
            filter_meta=filter_meta,
        )

        # Step 2: drop category filter if filtered search was empty
        if not chunks and filter_meta:
            logger.debug(
                "Category-filtered retrieve returned 0 chunks for '%s'; retrying unfiltered.", query
            )
            chunks = self._retrieve_candidates(
                query=query,
                query_vec=query_vec,
                vector_top_k=candidate_k,
                final_top_k=max(k, self.settings.hybrid_final_top_k),
            )

        # Step 3: query reformulation fallback
        if not chunks:
            for variant in _broaden_query(query, lead_profile):
                logger.debug("Zero chunks — retrying with broadened query: '%s'", variant)
                variant_vec = self.embedder.embed_text(variant)
                chunks = self._retrieve_candidates(
                    query=variant,
                    query_vec=variant_vec,
                    vector_top_k=candidate_k,
                    final_top_k=max(k, self.settings.hybrid_final_top_k),
                )
                if chunks:
                    logger.info(
                        "Query reformulation succeeded with variant: '%s' (%d chunks)",
                        variant,
                        len(chunks),
                    )
                    break

        # Step 4: seed fallback
        if not chunks:
            logger.info("All retrieve attempts failed — falling back to seed knowledge.")
            return seed_fallback_search(query, top_k=k, page_category=page_category) or []

        # Step 5: MMR diversification over the candidate pool → return top_k
        merged = _merge_page_chunks(chunks, page_url, self)
        merged = _apply_source_tier_filter(merged, exclude_source_tiers)
        return _mmr(query_vec, merged, top_k=k)

    def _retrieve_candidates(
        self,
        *,
        query: str,
        query_vec: list[float],
        vector_top_k: int,
        final_top_k: int,
        filter_meta: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.settings.hybrid_retrieval_enabled:
            return self.store.similarity_search(
                query_embedding=query_vec,
                top_k=vector_top_k,
                filter_metadata=filter_meta,
            )

        started = time.perf_counter()
        vector_started = time.perf_counter()
        vector_chunks = self.store.similarity_search(
            query_embedding=query_vec,
            top_k=vector_top_k,
            filter_metadata=filter_meta,
        )
        vector_latency_ms = (time.perf_counter() - vector_started) * 1000

        bm25_chunks: list[dict[str, Any]] = []
        bm25_loaded = False
        fallback_to_vector_only = False
        bm25_latency_ms = 0.0
        fusion_latency_ms = 0.0
        try:
            bm25_started = time.perf_counter()
            bm25_index = get_bm25_index(self.settings.bm25_index_path)
            bm25_loaded = True
            if bm25_index.doc_count == 0:
                raise ValueError("BM25 index is empty.")
            bm25_chunks = bm25_index.search(
                query,
                top_k=self.settings.bm25_top_k,
                filter_metadata=filter_meta,
                min_score=self.settings.hybrid_min_bm25_score,
            )
            bm25_latency_ms = (time.perf_counter() - bm25_started) * 1000
        except Exception as exc:
            if not self.settings.hybrid_fail_open:
                raise RuntimeError(f"BM25 retrieval failed: {exc}") from exc
            fallback_to_vector_only = True
            logger.warning("BM25 retrieval unavailable; falling back to vector-only retrieval: %s", exc)

        if fallback_to_vector_only:
            fused = vector_chunks
        else:
            fusion_started = time.perf_counter()
            fused = reciprocal_rank_fusion(
                vector_chunks,
                bm25_chunks,
                rrf_k=self.settings.hybrid_rrf_k,
                top_k=final_top_k,
            )
            fusion_latency_ms = (time.perf_counter() - fusion_started) * 1000

        retrieval_latency_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "Retrieval diagnostics: hybrid_enabled=%s vector_candidate_count=%d "
            "bm25_candidate_count=%d fused_candidate_count=%d final_candidate_count=%d "
            "bm25_index_loaded=%s fallback_to_vector_only=%s retrieval_latency_ms=%.2f "
            "vector_latency_ms=%.2f bm25_latency_ms=%.2f fusion_latency_ms=%.2f",
            self.settings.hybrid_retrieval_enabled,
            len(vector_chunks),
            len(bm25_chunks),
            len(fused),
            min(len(fused), final_top_k),
            bm25_loaded,
            fallback_to_vector_only,
            retrieval_latency_ms,
            vector_latency_ms,
            bm25_latency_ms,
            fusion_latency_ms,
        )
        return fused

    def ingest_chunks(
        self,
        chunks: list[PageChunk],
        skip_existing: bool = True,
        existing_hashes: set[str] | None = None,
    ) -> int:
        if not chunks:
            return 0

        if skip_existing:
            known = (
                existing_hashes
                if existing_hashes is not None
                else self.store.get_all_content_hashes()
            )
            chunks = deduplicate_chunks(chunks, existing_hashes=known)

        if not chunks:
            return 0

        texts = [c.embedding_text or c.chunk_text for c in chunks]
        embeddings = self.embedder.embed_batch(texts)
        self.store.add_chunks(chunks, embeddings)
        return len(chunks)


def _apply_source_tier_filter(
    chunks: list[dict[str, Any]],
    exclude_tiers: list[str] | None,
) -> list[dict[str, Any]]:
    if not exclude_tiers:
        return chunks
    filtered = [
        c for c in chunks if str(c.get("source_tier") or "core") not in exclude_tiers
    ]
    return filtered if filtered else chunks


def _merge_page_chunks(
    chunks: list[dict[str, Any]],
    page_url: str | None,
    retriever: Retriever,
) -> list[dict[str, Any]]:
    if not page_url or not get_settings().page_context_boost_enabled:
        return chunks
    page_chunks = retriever.fetch_chunks_for_page_url(page_url, limit=3)
    if not page_chunks:
        return chunks
    seen = {str(c.get("chunk_id") or "") for c in chunks if c.get("chunk_id")}
    merged = list(chunks)
    for chunk in page_chunks:
        cid = str(chunk.get("chunk_id") or "")
        if cid and cid in seen:
            continue
        if cid:
            seen.add(cid)
        merged.insert(0, chunk)
    return merged


_retriever: Retriever | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever
