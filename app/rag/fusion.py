from __future__ import annotations

import hashlib
from typing import Any


def stable_chunk_key(chunk: dict[str, Any]) -> str:
    if chunk.get("chunk_id"):
        return str(chunk["chunk_id"])
    source_url = str(chunk.get("source_url", ""))
    text = str(chunk.get("chunk_text", ""))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{source_url}:{digest}"


def reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    *,
    rrf_k: int = 60,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}

    _merge_ranked_results(fused, vector_results, path="vector", rrf_k=rrf_k)
    _merge_ranked_results(fused, bm25_results, path="bm25", rrf_k=rrf_k)

    ranked = sorted(
        fused.values(),
        key=lambda chunk: (
            -float(chunk.get("rrf_score", 0.0)),
            int(chunk.get("best_rank", 10**9)),
            stable_chunk_key(chunk),
        ),
    )
    for chunk in ranked:
        chunk["score"] = float(chunk.get("rrf_score", 0.0))
    return ranked[:top_k]


def _merge_ranked_results(
    fused: dict[str, dict[str, Any]],
    ranked_results: list[dict[str, Any]],
    *,
    path: str,
    rrf_k: int,
) -> None:
    for rank, candidate in enumerate(ranked_results, start=1):
        key = stable_chunk_key(candidate)
        existing = fused.get(key)
        contribution = 1.0 / (rrf_k + rank)
        if existing is None:
            existing = dict(candidate)
            existing["rrf_score"] = 0.0
            existing["retrieval_paths"] = []
            existing["best_rank"] = rank
            fused[key] = existing
        elif _candidate_has_better_metadata(candidate, existing):
            _copy_missing_metadata(candidate, existing)

        existing["rrf_score"] = float(existing.get("rrf_score", 0.0)) + contribution
        paths = existing.setdefault("retrieval_paths", [])
        if path not in paths:
            paths.append(path)
        existing["best_rank"] = min(int(existing.get("best_rank", rank)), rank)
        if path == "vector":
            existing.setdefault("vector_rank", rank)
            existing.setdefault("vector_score", candidate.get("score"))
            existing.setdefault("score", candidate.get("score", existing.get("rrf_score", 0.0)))
        else:
            existing.setdefault("bm25_rank", rank)
            existing.setdefault("bm25_score", candidate.get("bm25_score", candidate.get("score")))


def _candidate_has_better_metadata(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    existing_metadata = sum(1 for key, value in existing.items() if key != "chunk_text" and value)
    candidate_metadata = sum(1 for key, value in candidate.items() if key != "chunk_text" and value)
    return candidate_metadata > existing_metadata


def _copy_missing_metadata(candidate: dict[str, Any], existing: dict[str, Any]) -> None:
    for key, value in candidate.items():
        if key not in existing or existing[key] in (None, ""):
            existing[key] = value
