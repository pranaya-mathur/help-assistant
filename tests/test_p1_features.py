from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agent.lead_intelligence import compute_lead_scoring, should_offer_human_escalation
from app.agent.suggested_replies import build_suggested_replies, starter_chips_for_category
from app.api.chat_service import _extract_utm_params, _utm_from_context, process_escalate
from app.api.schemas import EscalateRequest
from app.rag.query_rewrite import clear_rewrite_cache, rewrite_query_for_retrieval
from app.rag.reranker import rerank_chunks


def test_heuristic_reranker_orders_by_overlap():
    chunks = [
        {"chunk_id": "a", "chunk_text": "MobCoder AI agent development services", "score": 0.55, "page_category": "ai_agents"},
        {"chunk_id": "b", "chunk_text": "MobCoder careers and hiring", "score": 0.56, "page_category": "careers"},
    ]
    ranked = rerank_chunks("AI agent development", chunks, top_k=2)
    assert ranked[0]["chunk_id"] == "a"
    assert ranked[0]["rerank_score"] > ranked[1]["rerank_score"]


@patch("app.rag.reranker.httpx.Client")
def test_cohere_reranker_backend(mock_client_cls):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "results": [{"index": 1, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.12}],
    }
    mock_resp.raise_for_status = MagicMock()
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.return_value = mock_resp
    mock_client_cls.return_value = mock_client

    chunks = [
        {"chunk_id": "a", "chunk_text": "first", "score": 0.1},
        {"chunk_id": "b", "chunk_text": "second", "score": 0.2},
    ]
    with patch("app.rag.reranker.get_settings") as mock_settings:
        settings = MagicMock()
        settings.reranker_backend = "cohere"
        settings.cohere_api_key = "test-key"
        settings.cohere_rerank_model = "rerank-english-v3.0"
        settings.reranker_top_k = 2
        mock_settings.return_value = settings
        ranked = rerank_chunks("query", chunks, top_k=2)

    assert ranked[0]["chunk_id"] == "b"
    assert ranked[0]["rerank_score"] == pytest.approx(0.99)


def test_query_rewrite_disabled_augments_with_project_need():
    clear_rewrite_cache()
    rewritten = rewrite_query_for_retrieval(
        "Tell me about services",
        profile={"project_need": "AI chatbot"},
        llm_text_fn=lambda *_a, **_k: "ignored",
    )
    assert "AI chatbot" in rewritten


def test_query_rewrite_uses_llm_when_enabled():
    clear_rewrite_cache()

    def fake_llm(_messages, max_tokens=80):
        return "MobCoder HIPAA healthcare AI services"

    with patch("app.rag.query_rewrite.get_settings") as mock_settings:
        settings = MagicMock()
        settings.query_rewrite_enabled = True
        mock_settings.return_value = settings
        rewritten = rewrite_query_for_retrieval(
            "can you tell me a bit more about what you do for companies like ours?",
            session_id="sess-1",
            llm_text_fn=fake_llm,
        )
    assert "HIPAA" in rewritten


def test_icp_scoring_hot_vs_cold():
    hot = compute_lead_scoring(
        {
            "name": "Alex",
            "email": "alex@co.com",
            "project_need": "AI agent",
            "project_type": "ai_agent",
            "timeline": "6 weeks",
            "budget_band": "$150k",
            "role": "cto",
        },
        "sales",
        user_query="Need launch in 6 weeks budget $150k",
    )
    cold = compute_lead_scoring({"project_need": "just exploring"}, "sales", user_query="hi")
    assert hot["lead_score"] == "hot"
    assert cold["lead_score"] == "cold"
    assert hot["lead_score_numeric"] > cold["lead_score_numeric"]


def test_human_escalation_detection():
    assert should_offer_human_escalation(
        {"user_query": "I'd like to speak with someone directly", "intent": "help", "stage": "discover"}
    )
    assert should_offer_human_escalation(
        {"user_query": "pricing", "intent": "sales", "stage": "qualify"}
    )
    assert not should_offer_human_escalation(
        {"user_query": "pricing", "intent": "help", "stage": "discover"}
    )


def test_human_escalation_common_phrasings():
    from app.agent.lead_intelligence import is_frustrated, requests_human

    wants_human = [
        "I want to talk to a real human right now",
        "Connect me to a person",
        "Can I chat with a live agent?",
        "get me a human",
        "put me through to sales",
        "transfer me to support",
        "I'd rather email someone",
        "Are you a bot? I need a real person",
    ]
    for phrase in wants_human:
        assert requests_human(phrase), phrase
        assert should_offer_human_escalation(
            {"user_query": phrase, "intent": "help", "stage": "discover"}
        ), phrase

    frustrated = [
        "This bot is useless",
        "You keep giving me vague answers",
        "I've asked twice already",
        "this is not helping at all",
    ]
    for phrase in frustrated:
        assert is_frustrated(phrase), phrase

    # Ordinary product questions must not trip either detector.
    for phrase in [
        "What services does MobCoder offer?",
        "How do you handle support after launch?",
        "Do you build chatbot agents?",
    ]:
        assert not requests_human(phrase), phrase
        assert not is_frustrated(phrase), phrase


def test_generate_answer_hands_off_on_human_request():
    from app.agent.nodes import build_generate_messages, generate_answer

    state = {
        "user_query": "This is useless. Connect me to a person right now.",
        "intent": "help",
        "stage": "discover",
        "retrieved_chunks": [{"chunk_id": "c1", "chunk_text": "MobCoder services."}],
    }
    assert build_generate_messages(state) is None
    out = generate_answer(state)
    assert "contact the team directly" in out["answer"].lower()
    assert out["citations"] == []
    # No sales pitch: the canned handoff must not mention case studies.
    assert "TIFIN" not in out["answer"]


def test_final_response_rewrites_unknown_opener():
    from app.agent.nodes import build_final_response

    state = {
        "user_query": "How big is your team?",
        "intent": "help",
        "stage": "discover",
        "answer": (
            "I don't have specific information about the exact size of our team. "
            "However, we have launched over 300 apps since 2014."
        ),
        "citations": [],
    }
    out = build_final_response(state)
    resp = out["final_response"]
    assert not resp.lower().startswith("i don't have")
    assert "isn't published on mobcoder.ai" in resp
    assert "300 apps" in resp


def test_final_response_keeps_normal_openers():
    from app.agent.nodes import build_final_response

    state = {
        "user_query": "What services do you offer?",
        "intent": "help",
        "stage": "discover",
        "answer": "MobCoder offers AI development and mobile engineering.",
        "citations": [],
    }
    out = build_final_response(state)
    assert out["final_response"].startswith("MobCoder offers AI development")


def test_suggested_replies_include_human_escalation_chip():
    replies = build_suggested_replies(
        intent="sales",
        stage="qualify",
        page_category="services",
        profile_question="",
        needs_contact_info=False,
        lead_profile={},
        show_human_escalation=True,
    )
    assert "Talk to our team" in replies


def test_starter_chips_for_ai_page():
    chips = starter_chips_for_category("ai_agents")
    assert len(chips) == 3
    assert any("AI" in chip for chip in chips)


def test_utm_context_prefers_session_metadata():
    utm = _utm_from_context(
        "https://mobcoder.ai/services?utm_source=google",
        {"utm_params": {"utm_source": "newsletter"}},
    )
    assert utm["utm_source"] == "newsletter"


def test_extract_utm_params():
    params = _extract_utm_params("https://mobcoder.ai/?utm_source=ads&utm_medium=cpc")
    assert params["utm_source"] == "ads"
    assert params["utm_medium"] == "cpc"


@patch("app.api.chat_service.dispatch_qualified_lead_async")
def test_process_escalate_dispatches_human_source(mock_dispatch):
    req = EscalateRequest(
        name="Jane Doe",
        email="jane@acme.com",
        message="Please contact me about enterprise AI.",
        page_url="https://mobcoder.ai/contact?utm_source=google",
        lead_consent=True,
    )
    resp = process_escalate(req)
    assert resp.ok is True
    assert resp.session_id
    mock_dispatch.assert_called_once()
    _args, kwargs = mock_dispatch.call_args
    assert kwargs["context"]["source"] == "human_escalation"
    assert kwargs["lead_profile"]["email"] == "jane@acme.com"


def test_escalate_request_defaults_consent_false():
    req = EscalateRequest(name="Jane Doe", email="jane@acme.com", message="Hi")
    assert req.lead_consent is False


@patch("app.api.chat_service.dispatch_qualified_lead_async")
def test_process_escalate_skips_dispatch_without_consent(mock_dispatch):
    req = EscalateRequest(
        name="Jane Doe",
        email="jane@acme.com",
        message="Please contact me about enterprise AI.",
        lead_consent=False,
    )
    resp = process_escalate(req)
    assert resp.ok is True
    mock_dispatch.assert_not_called()
