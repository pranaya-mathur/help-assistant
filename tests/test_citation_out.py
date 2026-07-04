from app.api.chat_service import _citation_snippet_from_text, state_to_response


def test_citation_snippet_from_text_strips_source_prefix():
    text = "Source: Mobcoder AI built a fintech wallet with React Native and secure payments."
    snippet = _citation_snippet_from_text(text)
    assert not snippet.lower().startswith("source:")
    assert "fintech wallet" in snippet


def test_citation_snippet_truncates_long_text():
    long_text = "A" * 200
    snippet = _citation_snippet_from_text(long_text)
    assert len(snippet) <= 140
    assert snippet.endswith("...")


def test_state_to_response_populates_snippet_and_page_category():
    state = {
        "final_response": "Here is a case study.",
        "request_id": "req-1",
        "intent": "help",
        "page_category": "case_studies",
        "citations": [
            {
                "page_title": "Fintech Wallet",
                "source_url": "https://mobcoder.ai/case-studies/fintech-wallet",
                "citation_text": "Source: Delivered a mobile wallet with biometric login.",
                "score": 0.91,
                "page_category": "case_studies",
            }
        ],
        "lead_profile": {},
        "suggested_replies": [],
    }
    resp = state_to_response(state, "sess-1")
    assert len(resp.citations) == 1
    cite = resp.citations[0]
    assert cite.page_category == "case_studies"
    assert cite.snippet
    assert "wallet" in cite.snippet.lower()
