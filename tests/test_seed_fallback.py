from app.rag.seed_fallback import seed_fallback_search


def test_seed_fallback_returns_chunks():
    results = seed_fallback_search("AI chatbot customer support", top_k=4)
    assert len(results) >= 1
    assert "mobcoder.ai" in results[0]["source_url"]
