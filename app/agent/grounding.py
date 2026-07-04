from __future__ import annotations

from abc import ABC, abstractmethod
import re
from typing import Any, Callable

from app.agent.claim_validator import GroundingResult, enforce_grounding


class GroundingProvider(ABC):
    @abstractmethod
    def validate(
        self,
        answer: str,
        chunks: list[dict[str, Any]],
        llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, GroundingResult]:
        ...


class HeuristicGroundingProvider(GroundingProvider):
    def validate(
        self,
        answer: str,
        chunks: list[dict[str, Any]],
        llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, GroundingResult]:
        fixed, result = enforce_grounding(answer, chunks)
        if llm_json_fn:
            fixed, result = _llm_claim_check(fixed, chunks, llm_json_fn, result)
        return fixed, result


class SovereignGroundingProvider(GroundingProvider):
    """Stub for future Sovereign-AI integration; delegates to heuristic today."""

    def __init__(self, fallback: GroundingProvider | None = None) -> None:
        self._fallback = fallback or HeuristicGroundingProvider()

    def validate(
        self,
        answer: str,
        chunks: list[dict[str, Any]],
        llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    ) -> tuple[str, GroundingResult]:
        return self._fallback.validate(answer, chunks, llm_json_fn=llm_json_fn)


def _llm_claim_check(
    answer: str,
    chunks: list[dict[str, Any]],
    llm_json_fn: Callable[[str], dict[str, Any]],
    prior: GroundingResult,
) -> tuple[str, GroundingResult]:
    context = "\n\n".join(
        f"{c.get('page_title', '')}: {c.get('chunk_text', '')[:4000]}"
        for c in chunks[:5]
    )
    try:
        data = llm_json_fn(
            "Given ONLY the context below, list factual claims in the answer "
            "that are NOT supported by the context.\n"
            f"Context:\n{context}\n\nAnswer:\n{answer}\n\n"
            'Return JSON: {"unsupported_claims":["..."]}'
        )
        claims = data.get("unsupported_claims") or []
        if not isinstance(claims, list) or not claims:
            return answer, prior
        context_tokens = _grounding_tokens(context)
        filtered_claims = [
            claim
            for claim in claims
            if not (
                prior.is_grounded
                and isinstance(claim, str)
                and _claim_context_overlap(claim, context_tokens) >= 0.18
            )
        ]
        if not filtered_claims:
            return answer, prior
        trimmed = answer
        for claim in filtered_claims[:3]:
            if isinstance(claim, str) and claim in trimmed:
                trimmed = trimmed.replace(claim, "")
        note = (
            "\n\n*Note: Some details could not be verified against mobcoder.ai; "
            "please confirm with our team.*"
        )
        if note not in trimmed:
            trimmed += note
        return trimmed, GroundingResult(False, True, [str(c) for c in filtered_claims[:5]])
    except Exception:
        return answer, prior


def _grounding_tokens(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-z0-9]{4,}", text)}


def _claim_context_overlap(claim: str, context_tokens: set[str]) -> float:
    claim_tokens = _grounding_tokens(claim)
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & context_tokens) / len(claim_tokens)


_provider: GroundingProvider | None = None


def get_grounding_provider() -> GroundingProvider:
    global _provider
    if _provider is None:
        from app.config.settings import get_settings

        if get_settings().grounding_provider == "sovereign":
            _provider = SovereignGroundingProvider()
        else:
            _provider = HeuristicGroundingProvider()
    return _provider
