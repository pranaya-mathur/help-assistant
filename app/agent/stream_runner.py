from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.agent.nodes import (
    LLM_FAILURE_MESSAGE,
    _llm_text_stream,
    append_qualification,
    build_final_response,
    build_generate_messages,
    generate_answer,
    retrieve_sources,
    safety_check,
    validate_citations,
    validate_grounding,
)
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Thread pool for short CPU / I/O-bound node work. Streaming runs on a
# dedicated daemon thread so it never blocks these workers (pool exhaustion
# was causing 30–60s delays when users sent messages quickly).
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent-node")


class _StreamError:
    """Sentinel placed on the token queue when the LLM stream raises."""


_STREAM_ERROR = _StreamError()


async def _run_in_executor(fn: Any, *args: Any) -> Any:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_EXECUTOR, fn, *args)


# ---------------------------------------------------------------------------
# Retrieve-first hot path (pilot)
# ---------------------------------------------------------------------------
# safety_check (rules only) → resolve_retrieval_plan + retrieve → stream answer
# Intent, profile LLM, and lead scoring run post-response (see post_response.py).
# ---------------------------------------------------------------------------


async def _run_retrieval_first_async(state: dict[str, Any]) -> dict[str, Any]:
    """Safety → retrieve. No LLM calls before first token."""

    async def _timed(name: str, fn: Any, *args: Any) -> Any:
        start = time.perf_counter()
        result = await _run_in_executor(fn, *args)
        timings = dict(result.get("step_timings_ms") or {})
        timings[name] = round((time.perf_counter() - start) * 1000, 2)
        result["step_timings_ms"] = timings
        return result

    settings = get_settings()
    timings: dict[str, float] = {}

    start_safety = time.perf_counter()
    merged = safety_check(state)
    timings["safety_check"] = round((time.perf_counter() - start_safety) * 1000, 2)

    if not merged.get("is_safe", True):
        merged["retrieved_chunks"] = []
        merged["current_page_chunks"] = []
        merged["step_timings_ms"] = timings
        return merged

    if settings.is_help_mode():
        merged = {
            **merged,
            "intent": "help",
            "stage": "discover",
            "needs_contact_info": False,
        }

    merged["step_timings_ms"] = timings
    return await _timed("retrieve_sources", retrieve_sources, merged)


def _run_post_generate(state: dict[str, Any]) -> dict[str, Any]:
    if not state.get("answer"):
        state = generate_answer(state)
    else:
        state = validate_grounding(state)
        state = validate_citations(state)
    state = append_qualification(state)
    return build_final_response(state)


async def run_agent_stream_events(
    *,
    user_query: str,
    conversation_history: list[dict[str, str]] | None = None,
    lead_profile: dict[str, Any] | None = None,
    prior_intent: str = "",
    page_url: str = "",
    page_title: str = "",
    request_id: str = "",
    lead_acknowledged: bool = False,
    session_context: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    from app.agent.state import default_state

    state: dict[str, Any] = dict(
        default_state(
            user_query=user_query,
            conversation_history=conversation_history,
            lead_profile=lead_profile,
            prior_intent=prior_intent,
            page_url=page_url,
            page_title=page_title,
            request_id=request_id,
            lead_acknowledged=lead_acknowledged,
            session_context=session_context,
        )
    )

    state = await _run_retrieval_first_async(state)

    if not state.get("is_safe", True):
        state = build_final_response({**state, "answer": state.get("final_response", "")})
        yield {
            "type": "done",
            "response": state.get("final_response", ""),
            "state": state,
        }
        return

    chunks = state.get("retrieved_chunks", [])

    if not chunks:
        start_generate = time.perf_counter()
        state = await _run_in_executor(generate_answer, state)
        state["step_timings_ms"] = {
            **(state.get("step_timings_ms") or {}),
            "generate_answer": round((time.perf_counter() - start_generate) * 1000, 2),
        }
        state = _run_post_generate(state)
        yield {
            "type": "done",
            "response": state.get("final_response", ""),
            "state": state,
        }
        return

    messages = build_generate_messages(state)
    if not messages:
        start_generate = time.perf_counter()
        state = await _run_in_executor(generate_answer, state)
        state["step_timings_ms"] = {
            **(state.get("step_timings_ms") or {}),
            "generate_answer": round((time.perf_counter() - start_generate) * 1000, 2),
        }
        state = _run_post_generate(state)
        yield {
            "type": "done",
            "response": state.get("final_response", ""),
            "state": state,
        }
        return

    citations = []
    for chunk in chunks:
        from app.agent.nodes import _citation_from_chunk

        citations.append(_citation_from_chunk(chunk))
    state["citations"] = citations

    buffer = ""
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None | _StreamError] = asyncio.Queue()

    stream_started = time.perf_counter()

    def _produce_tokens() -> None:
        try:
            for token in _llm_text_stream(messages):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as exc:
            logger.error("stream token error: %s", exc)
            loop.call_soon_threadsafe(queue.put_nowait, _STREAM_ERROR)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=_produce_tokens, daemon=True).start()

    stream_failed = False
    while True:
        token = await queue.get()
        if token is None:
            break
        if isinstance(token, _StreamError):
            stream_failed = True
            continue
        buffer += token
        yield {"type": "token", "content": token}

    timings = dict(state.get("step_timings_ms") or {})
    timings["stream_generate_answer"] = round((time.perf_counter() - stream_started) * 1000, 2)
    state["step_timings_ms"] = timings

    if stream_failed and not buffer:
        state["answer"] = LLM_FAILURE_MESSAGE
        state["citations"] = []
    else:
        if stream_failed:
            buffer += (
                "\n\n*(This response was interrupted — please resend your "
                "message if it looks incomplete.)*"
            )
        state["answer"] = buffer
        state = validate_grounding(state)
        state = validate_citations(state)

    state = append_qualification(state)
    state = build_final_response(state)

    final = state.get("final_response", "")
    yield {"type": "done", "response": final, "state": state}
