from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.config.settings as settings_mod
from app.crawler.schema import PageChunk
from app.rag.bm25_index import (
    BM25Index,
    build_bm25_index,
    get_bm25_index,
    reset_bm25_index_cache,
    tokenize,
)
from app.rag.fusion import reciprocal_rank_fusion


def _chunk(chunk_id: str, text: str, category: str = "services") -> PageChunk:
    chunk = PageChunk(
        chunk_id=chunk_id,
        page_id=f"page-{chunk_id}",
        source_url=f"https://mobcoder.ai/{chunk_id}",
        page_title=f"Page {chunk_id}",
        page_category=category,  # type: ignore[arg-type]
        content_type="capabilities",
        section="Overview",
        chunk_text=text,
        token_count=len(text.split()),
        citation_text=f"Source: Page {chunk_id}",
    )
    chunk.embedding_text = chunk.build_embedding_text()
    return chunk


def _fake_corpus() -> list[PageChunk]:
    return [
        _chunk("health", "MobCoder builds HIPAA-compliant healthcare AI platforms."),
        _chunk("mobile", "MobCoder develops Flutter and React Native mobile apps."),
        _chunk(
            "staff",
            "MobCoder provides staff augmentation and dedicated development teams.",
        ),
    ]


def test_bm25_tokenization_preserves_short_technical_terms() -> None:
    tokens = tokenize("AI, RAG, LLM, HIPAA, SOC2, GDPR, API, Flutter, React Native.")

    assert {"ai", "rag", "llm", "hipaa", "soc2", "gdpr", "api"} <= set(tokens)
    assert "flutter" in tokens
    assert "react" in tokens
    assert "native" in tokens


def test_bm25_index_build_persist_load_and_exact_search(tmp_path: Path) -> None:
    path = tmp_path / "bm25.pkl"
    build_bm25_index(_fake_corpus(), path)

    index = BM25Index.load(path)
    results = index.search("HIPAA AI", top_k=2)

    assert index.doc_count == 3
    assert results[0]["chunk_id"] == "health"
    assert results[0]["bm25_score"] > 0
    assert index.search("Flutter app", top_k=1)[0]["chunk_id"] == "mobile"
    assert index.search("staff augmentation", top_k=1)[0]["chunk_id"] == "staff"


def test_bm25_index_save_is_atomic_on_crash(tmp_path: Path, monkeypatch) -> None:
    """D4 regression: save() used to pickle.dump directly into the target path —
    a crash/kill mid-write left a truncated, corrupt index file. Now it writes to
    a temp file and os.replace()s over the target, so a crash can never corrupt
    the previously-saved, still-valid index."""
    import pickle

    path = tmp_path / "index.pkl"
    v1 = BM25Index.from_chunks([_chunk("c1", "hello world")])
    v1.save(path)
    original_size = path.stat().st_size

    def crashing_dump(obj, fh):
        fh.write(b"PARTIAL-GARBAGE-NOT-A-REAL-PICKLE")
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(pickle, "dump", crashing_dump)
    v2 = BM25Index.from_chunks([_chunk("c1", "hello world"), _chunk("c2", "goodbye world")])
    with pytest.raises(RuntimeError):
        v2.save(path)
    monkeypatch.undo()

    # No leftover temp file, and the original file is byte-for-byte untouched.
    assert list(tmp_path.glob(".*.tmp")) == []
    assert path.stat().st_size == original_size
    reloaded = BM25Index.load(path)
    assert reloaded.doc_count == 1


def test_cached_bm25_index_reloads_from_path(tmp_path: Path) -> None:
    path = tmp_path / "cached.pkl"
    reset_bm25_index_cache()
    build_bm25_index(_fake_corpus(), path)

    first = get_bm25_index(path)
    second = get_bm25_index(path)

    assert first is second
    assert second.search("Flutter app", top_k=1)[0]["chunk_id"] == "mobile"


def test_rrf_deduplicates_boosts_and_preserves_metadata() -> None:
    vector = [
        {"chunk_id": "a", "chunk_text": "semantic only", "score": 0.9},
        {"chunk_id": "b", "chunk_text": "both", "score": 0.6, "source_url": "https://mobcoder.ai/b"},
    ]
    bm25 = [
        {"chunk_id": "b", "chunk_text": "both", "score": 3.0, "bm25_score": 3.0},
        {"chunk_id": "c", "chunk_text": "lexical only", "score": 2.0},
    ]

    fused = reciprocal_rank_fusion(vector, bm25, rrf_k=60, top_k=3)

    assert [item["chunk_id"] for item in fused] == ["b", "a", "c"]
    assert fused[0]["retrieval_paths"] == ["vector", "bm25"]
    assert fused[0]["source_url"] == "https://mobcoder.ai/b"
    assert fused[0]["vector_rank"] == 2
    assert fused[0]["bm25_rank"] == 1


class _FakeEmbedder:
    def embed_text(self, query: str) -> list[float]:
        return [float(len(query)), 1.0]


class _FakeStore:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    def count(self) -> int:
        return 99

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append({"top_k": top_k, "filter_metadata": filter_metadata})
        return self.chunks[:top_k]


def _retriever_with_store(monkeypatch: pytest.MonkeyPatch, fake_store: _FakeStore):
    import app.rag.retriever as retriever_mod

    monkeypatch.setattr(retriever_mod, "get_vector_store", lambda: fake_store)
    retriever = retriever_mod.Retriever()
    retriever._embedder = _FakeEmbedder()
    return retriever


def test_retriever_hybrid_disabled_uses_vector_only_path(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_mod.get_settings.cache_clear()
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "false")
    fake_store = _FakeStore([{"chunk_id": "vector", "chunk_text": "vector", "score": 0.8}])
    retriever = _retriever_with_store(monkeypatch, fake_store)

    results = retriever.retrieve("HIPAA AI", top_k=1)

    assert results[0]["chunk_id"] == "vector"
    assert fake_store.calls[0]["top_k"] == retriever.settings.retrieval_candidate_k


def test_retriever_fail_open_falls_back_to_vector_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("HYBRID_FAIL_OPEN", "true")
    monkeypatch.setenv("BM25_INDEX_PATH", str(tmp_path / "missing.pkl"))
    fake_store = _FakeStore([{"chunk_id": "vector", "chunk_text": "vector", "score": 0.8}])
    retriever = _retriever_with_store(monkeypatch, fake_store)

    results = retriever.retrieve("HIPAA AI", top_k=1)

    assert results[0]["chunk_id"] == "vector"
    assert fake_store.calls[0]["top_k"] == retriever.settings.vector_top_k


def test_retriever_fail_open_handles_empty_bm25_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()
    index_path = tmp_path / "empty.pkl"
    BM25Index.from_chunks([]).save(index_path)
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("HYBRID_FAIL_OPEN", "true")
    monkeypatch.setenv("BM25_INDEX_PATH", str(index_path))
    fake_store = _FakeStore([{"chunk_id": "vector", "chunk_text": "vector", "score": 0.8}])
    retriever = _retriever_with_store(monkeypatch, fake_store)

    results = retriever.retrieve("HIPAA AI", top_k=1)

    assert results[0]["chunk_id"] == "vector"


def test_retriever_fail_closed_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("HYBRID_FAIL_OPEN", "false")
    monkeypatch.setenv("BM25_INDEX_PATH", str(tmp_path / "missing.pkl"))
    fake_store = _FakeStore([{"chunk_id": "vector", "chunk_text": "vector", "score": 0.8}])
    retriever = _retriever_with_store(monkeypatch, fake_store)

    with pytest.raises(RuntimeError, match="BM25 retrieval failed"):
        retriever.retrieve("HIPAA AI", top_k=1)


class _EmptyFakeStore(_FakeStore):
    """Simulates a Chroma collection reporting 0 docs (e.g. after a partial wipe)."""

    def count(self) -> int:
        return 0


def test_retriever_uses_bm25_when_chroma_empty_but_bm25_populated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """D5 regression: retrieve() used to short-circuit to the tiny bundled seed
    corpus whenever store.count() == 0, even if hybrid was enabled and the BM25
    index on disk was fully populated (e.g. Chroma wiped but BM25 untouched).
    It should still retrieve real content via BM25 instead of degrading to seed."""
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()
    index_path = tmp_path / "populated.pkl"
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("HYBRID_FAIL_OPEN", "true")
    monkeypatch.setenv("BM25_INDEX_PATH", str(index_path))
    build_bm25_index(_fake_corpus(), index_path)
    fake_store = _EmptyFakeStore([])
    retriever = _retriever_with_store(monkeypatch, fake_store)

    results = retriever.retrieve("Flutter app", top_k=1)

    assert results
    assert results[0]["chunk_id"] == "mobile"
    # Confirms it took the real retrieval path, not the bundled seed fallback
    # (seed_fallback content wouldn't carry this exact chunk_id).


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("HIPAA AI", "health"),
        ("Flutter app", "mobile"),
        ("dedicated developers", "staff"),
    ],
)
def test_hybrid_retrieval_quality_smoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    query: str,
    expected: str,
) -> None:
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()
    index_path = tmp_path / "bm25.pkl"
    monkeypatch.setenv("HYBRID_RETRIEVAL_ENABLED", "true")
    monkeypatch.setenv("BM25_INDEX_PATH", str(index_path))
    monkeypatch.setenv("HYBRID_FINAL_TOP_K", "3")
    settings_mod.get_settings.cache_clear()
    build_bm25_index(_fake_corpus(), index_path)

    vector_chunks = [
        {"chunk_id": "mobile", "chunk_text": "MobCoder develops Flutter apps.", "score": 0.9},
        {"chunk_id": "health", "chunk_text": "MobCoder builds healthcare AI.", "score": 0.7},
        {"chunk_id": "staff", "chunk_text": "MobCoder provides staff augmentation.", "score": 0.6},
    ]
    fake_store = _FakeStore(vector_chunks)
    retriever = _retriever_with_store(monkeypatch, fake_store)

    results = retriever.retrieve(query, top_k=2)

    assert expected in [result["chunk_id"] for result in results[:2]]
