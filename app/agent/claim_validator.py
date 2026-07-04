from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

# Claims that must be supported by retrieved context
_HIGH_RISK_PATTERNS = re.compile(
    r"\$|\b\d+\s*%|\bguarantee\b|\balways\b|\bnever\b|\bclients?\s+include\b|"
    r"\bwe\s+are\s+the\s+(best|largest|leading)\b|\b\d+\+\s+years\b|"
    r"\b(fixed|starting)\s+price\b|\ball\s+projects\b",
    re.I,
)


@dataclass
class GroundingResult:
    is_grounded: bool
    rewritten: bool
    unsupported_claims: list[str]


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-z0-9]{4,}", text)}


def enforce_grounding(
    answer: str,
    chunks: list[dict[str, Any]],
    llm_json_fn: Callable[[str], dict[str, Any]] | None = None,
    llm_text_fn: Callable[[list[dict[str, str]]], str] | None = None,
) -> tuple[str, GroundingResult]:
    if not answer or not chunks:
        return answer, GroundingResult(True, False, [])

    context_tokens: set[str] = set()
    for c in chunks:
        context_tokens |= _tokenize(c.get("chunk_text", ""))
        context_tokens |= _tokenize(c.get("page_title", ""))
        context_tokens |= _tokenize(c.get("source_url", ""))

    sentences = re.split(r"(?<=[.!?])\s+", answer)
    unsupported: list[str] = []

    for sent in sentences:
        stripped = sent.strip()
        if len(stripped) < 35:
            continue
        if "http" in sent or "mobcoder.ai" in sent.lower():
            continue
        if stripped.startswith("*") and stripped.endswith("*"):
            continue  # qualification footer

        sent_tokens = _tokenize(sent)
        if not sent_tokens:
            continue

        overlap = len(sent_tokens & context_tokens) / max(len(sent_tokens), 1)
        is_risky = bool(_HIGH_RISK_PATTERNS.search(sent))

        if is_risky and overlap < 0.2:
            unsupported.append(stripped)
        elif not is_risky and overlap < 0.08 and len(sent_tokens) >= 8:
            # Long factual-sounding sentence with almost no source overlap
            if any(
                w in sent.lower()
                for w in (
                    "offer", "provide", "specialize", "expert", "deliver",
                    "build", "develop", "platform", "solution",
                )
            ):
                unsupported.append(stripped)

    if unsupported:
        trimmed = answer
        for s in unsupported[:3]:
            trimmed = trimmed.replace(s, "")
        trimmed = re.sub(r"\n{3,}", "\n\n", trimmed).strip()
        note = (
            "\n\n*Note: Some details could not be verified against mobcoder.ai; "
            "please confirm with our team.*"
        )
        if note not in trimmed:
            trimmed += note
        return trimmed, GroundingResult(False, True, unsupported)

    return answer, GroundingResult(True, False, [])
