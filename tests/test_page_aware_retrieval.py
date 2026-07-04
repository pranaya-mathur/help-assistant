from __future__ import annotations

from app.rag.reranker import _heuristic_rerank


def test_page_url_boost_ranks_matching_chunk_first(monkeypatch):
    monkeypatch.setenv("PAGE_CONTEXT_BOOST_ENABLED", "true")
    from app.config.settings import get_settings

    get_settings.cache_clear()
    chunks = [
        {
            "chunk_id": "a",
            "chunk_text": "General Mobcoder services overview.",
            "source_url": "https://mobcoder.ai/services",
            "score": 0.9,
        },
        {
            "chunk_id": "b",
            "chunk_text": "Fintech wallet case study details.",
            "source_url": "https://mobcoder.ai/case-studies/fintech-wallet",
            "score": 0.7,
        },
    ]
    ranked = _heuristic_rerank(
        "tell me more",
        chunks,
        top_k=2,
        page_url="https://mobcoder.ai/case-studies/fintech-wallet",
    )
    assert ranked[0]["chunk_id"] == "b"


def test_page_url_boost_disabled_when_no_page_url():
    chunks = [
        {
            "chunk_id": "a",
            "chunk_text": "General overview.",
            "source_url": "https://mobcoder.ai/services",
            "score": 0.5,
        },
        {
            "chunk_id": "b",
            "chunk_text": "Case study.",
            "source_url": "https://mobcoder.ai/case-studies/foo",
            "score": 0.9,
        },
    ]
    ranked = _heuristic_rerank("case study", chunks, top_k=2, page_url=None)
    assert ranked[0]["chunk_id"] == "b"
