from __future__ import annotations

import logging
import threading
import time
from typing import Any

from langgraph.graph import END, StateGraph

from app.observability.tracing import span

from app.agent.nodes import (
    append_qualification,
    build_final_response,
    generate_answer,
    retrieve_sources,
    safety_check,
    validate_citations,
    validate_grounding,
)
from app.agent.state import AgentState, default_state
from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def _route_after_safety(state: dict[str, Any]) -> str:
    return "__end__" if not state.get("is_safe", True) else "retrieve_sources"


def _prepare_help_defaults(state: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    if not settings.is_help_mode():
        return state
    return {
        **state,
        "intent": "help",
        "stage": "discover",
        "needs_contact_info": False,
    }


def _build_graph() -> Any:
    graph: StateGraph = StateGraph(AgentState)

    def timed(name: str, fn: Any) -> Any:
        def _wrapped(state: dict[str, Any]) -> dict[str, Any]:
            start = time.perf_counter()
            with span(name, {"session.request_id": state.get("request_id")}):
                out = fn(state)
            timings = dict(out.get("step_timings_ms") or state.get("step_timings_ms") or {})
            timings[name] = round((time.perf_counter() - start) * 1000, 2)
            return {**out, "step_timings_ms": timings}

        return _wrapped

    graph.add_node("safety_check", timed("safety_check", safety_check))
    graph.add_node(
        "prepare_defaults",
        timed("prepare_defaults", _prepare_help_defaults),
    )
    graph.add_node("retrieve_sources", timed("retrieve_sources", retrieve_sources))
    graph.add_node("generate_answer", timed("generate_answer", generate_answer))
    graph.add_node("validate_grounding", timed("validate_grounding", validate_grounding))
    graph.add_node("validate_citations", timed("validate_citations", validate_citations))
    graph.add_node("append_qualification", timed("append_qualification", append_qualification))
    graph.add_node("build_final_response", timed("build_final_response", build_final_response))

    graph.set_entry_point("safety_check")
    graph.add_conditional_edges(
        "safety_check",
        _route_after_safety,
        {
            "__end__": END,
            "retrieve_sources": "prepare_defaults",
        },
    )
    graph.add_edge("prepare_defaults", "retrieve_sources")
    graph.add_edge("retrieve_sources", "generate_answer")
    graph.add_edge("generate_answer", "validate_grounding")
    graph.add_edge("validate_grounding", "validate_citations")
    graph.add_edge("validate_citations", "append_qualification")
    graph.add_edge("append_qualification", "build_final_response")
    graph.add_edge("build_final_response", END)

    return graph.compile()


_compiled_graph: Any = None
_graph_lock = threading.Lock()


def get_compiled_graph() -> Any:
    global _compiled_graph
    if _compiled_graph is None:
        with _graph_lock:
            if _compiled_graph is None:
                _compiled_graph = _build_graph()
    return _compiled_graph


def run_agent(
    user_query: str,
    conversation_history: list[dict[str, str]] | None = None,
    lead_profile: dict[str, Any] | None = None,
    prior_intent: str = "",
    page_url: str = "",
    page_title: str = "",
    request_id: str = "",
    lead_acknowledged: bool = False,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    initial = default_state(
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
    graph = get_compiled_graph()
    state = graph.invoke(initial)
    from app.agent.post_response import enrich_state

    return enrich_state(state)
