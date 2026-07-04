from __future__ import annotations

import pytest

import app.agent.nodes as nodes_mod
import app.agent.stream_runner as sr


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub retrieve-first hot path nodes so streaming reaches the token thread."""

    def fake_retrieve(state):
        return {
            **state,
            "retrieved_chunks": [
                {
                    "chunk_id": "c1",
                    "chunk_text": "MobCoder builds AI.",
                    "source_url": "https://mobcoder.ai/x",
                }
            ],
            "current_page_chunks": [],
        }

    def fake_safety(state):
        return {**state, "is_safe": True}

    def fake_build_messages(state):
        return [{"role": "user", "content": "hi"}]

    identity = lambda state: state  # noqa: E731

    def fake_build_final_response(state):
        return {**state, "final_response": state.get("answer", "")}

    monkeypatch.setattr(sr, "retrieve_sources", fake_retrieve)
    monkeypatch.setattr(sr, "safety_check", fake_safety)
    monkeypatch.setattr(sr, "build_generate_messages", fake_build_messages)
    monkeypatch.setattr(sr, "validate_grounding", identity)
    monkeypatch.setattr(sr, "validate_citations", identity)
    monkeypatch.setattr(sr, "append_qualification", identity)
    monkeypatch.setattr(sr, "build_final_response", fake_build_final_response)
    monkeypatch.setattr(nodes_mod, "_citation_from_chunk", lambda chunk: {"source_url": chunk.get("source_url", "")})


async def _collect(user_query: str) -> list[dict]:
    events = []
    async for event in sr.run_agent_stream_events(user_query=user_query):
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_stream_llm_failure_before_any_token_yields_explicit_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def failing_stream(messages, max_tokens=600):
        raise ValueError("simulated LLM outage")
        yield ""  # pragma: no cover

    monkeypatch.setattr(sr, "_llm_text_stream", failing_stream)

    events = await _collect("What AI services do you offer?")

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert token_events == []
    assert len(done_events) == 1
    assert done_events[0]["response"] == nodes_mod.LLM_FAILURE_MESSAGE
    assert done_events[0]["state"]["answer"] == nodes_mod.LLM_FAILURE_MESSAGE
    assert done_events[0]["state"]["citations"] == []


@pytest.mark.asyncio
async def test_stream_llm_failure_mid_stream_keeps_partial_text_with_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def failing_stream(messages, max_tokens=600):
        yield "MobCoder builds"
        yield " custom AI"
        raise ValueError("simulated mid-stream disconnect")

    monkeypatch.setattr(sr, "_llm_text_stream", failing_stream)

    events = await _collect("What AI services do you offer?")

    token_events = [e for e in events if e["type"] == "token"]
    done_events = [e for e in events if e["type"] == "done"]
    assert [e["content"] for e in token_events] == ["MobCoder builds", " custom AI"]
    assert len(done_events) == 1
    final = done_events[0]["response"]
    assert final.startswith("MobCoder builds custom AI")
    assert "interrupted" in final


@pytest.mark.asyncio
async def test_stream_succeeds_normally_without_error_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pipeline(monkeypatch)

    def clean_stream(messages, max_tokens=600):
        yield "MobCoder builds"
        yield " custom AI agents."

    monkeypatch.setattr(sr, "_llm_text_stream", clean_stream)

    events = await _collect("What AI services do you offer?")

    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["response"] == "MobCoder builds custom AI agents."
    assert "interrupted" not in done_events[0]["response"]
