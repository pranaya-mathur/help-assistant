#!/usr/bin/env python3
"""Compare vector-only and hybrid retrieval on a local MobCoder query set.

Default mode is intentionally offline: it loads the latest local chunk snapshot,
uses a deterministic TF-IDF style vector store, builds a temporary BM25 index,
and runs the real Retriever orchestration for vector-only vs hybrid behavior.
This avoids LLM calls while still exercising the hybrid integration, RRF, MMR,
metadata preservation, and fail-open handling.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.agent.retrieval_plan import resolve_retrieval_plan  # noqa: E402
from app.crawler.schema import PageChunk  # noqa: E402
from app.rag.bm25_index import build_bm25_index, reset_bm25_index_cache, tokenize  # noqa: E402

QUERY_PATH = ROOT / "data" / "evals" / "retrieval_queries.json"
RESULT_PATH = ROOT / "data" / "evals" / "retrieval_eval_results.json"
TOKEN_RE = re.compile(r"[a-z0-9]+")


class LocalTfidfEmbedder:
    def __init__(self, chunks: list[PageChunk]) -> None:
        docs = [tokenize(c.embedding_text or c.chunk_text) for c in chunks]
        doc_freq: Counter[str] = Counter()
        for tokens in docs:
            doc_freq.update(set(tokens))
        vocab_terms = sorted(doc_freq)
        self.vocab = {term: idx for idx, term in enumerate(vocab_terms)}
        total_docs = max(len(docs), 1)
        self.idf = {
            term: math.log((1 + total_docs) / (1 + df)) + 1.0
            for term, df in doc_freq.items()
        }

    def embed_text(self, text: str) -> list[float]:
        counts = Counter(tokenize(text))
        vec = [0.0] * len(self.vocab)
        for token, count in counts.items():
            idx = self.vocab.get(token)
            if idx is not None:
                vec[idx] = float(count) * self.idf.get(token, 1.0)
        norm = math.sqrt(sum(v * v for v in vec))
        if norm:
            vec = [v / norm for v in vec]
        return vec


class LocalVectorStore:
    def __init__(self, chunks: list[PageChunk], embedder: LocalTfidfEmbedder) -> None:
        self.rows: list[dict[str, Any]] = []
        for chunk in chunks:
            metadata = {
                "chunk_id": chunk.chunk_id,
                "page_id": chunk.page_id,
                "source_url": chunk.source_url,
                "page_title": chunk.page_title,
                "page_category": chunk.page_category,
                "source_tier": chunk.source_tier,
                "content_type": chunk.content_type,
                "section": chunk.section,
                "token_count": chunk.token_count,
                "citation_text": chunk.citation_text,
                "content_hash": chunk.content_hash,
                "source_freshness": chunk.metadata.source_freshness or "",
            }
            self.rows.append(
                {
                    "chunk_text": chunk.chunk_text,
                    "embedding": embedder.embed_text(chunk.embedding_text or chunk.chunk_text),
                    **metadata,
                }
            )

    def count(self) -> int:
        return len(self.rows)

    def similarity_search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        scored: list[dict[str, Any]] = []
        for row in self.rows:
            if filter_metadata and any(row.get(k) != v for k, v in filter_metadata.items()):
                continue
            score = _cosine(query_embedding, row["embedding"])
            item = dict(row)
            item["score"] = score
            scored.append(item)
        scored.sort(key=lambda c: (-float(c.get("score", 0.0)), str(c.get("chunk_id", ""))))
        return scored[:top_k]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def load_queries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"Retrieval query set not found: {path}")
    queries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(queries, list) or not queries:
        raise SystemExit(f"Retrieval query set is empty or invalid: {path}")
    return queries


def latest_chunk_snapshot() -> Path:
    chunks_dir = ROOT / "data" / "chunks"
    snapshots = sorted(chunks_dir.glob("chunks_*.json"))
    if not snapshots:
        raise SystemExit(
            "No chunk snapshots found. Run: python3 scripts/ingest.py --reset-collection"
        )
    return snapshots[-1]


def load_chunks(path: Path) -> list[PageChunk]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    chunks = [PageChunk.model_validate(item) for item in raw]
    if not chunks:
        raise SystemExit(f"Chunk snapshot is empty: {path}")
    return chunks


def make_retriever(
    *,
    hybrid_enabled: bool,
    store: LocalVectorStore,
    embedder: LocalTfidfEmbedder,
    bm25_index_path: Path,
):
    import app.config.settings as settings_mod
    import app.rag.retriever as retriever_mod

    os.environ["HYBRID_RETRIEVAL_ENABLED"] = "true" if hybrid_enabled else "false"
    os.environ["HYBRID_FAIL_OPEN"] = "true"
    os.environ["BM25_INDEX_PATH"] = str(bm25_index_path)
    settings_mod.get_settings.cache_clear()
    reset_bm25_index_cache()

    retriever = retriever_mod.Retriever()
    retriever.store = store
    retriever._embedder = embedder
    return retriever


def run_retriever(
    retriever: Any,
    *,
    query: str,
    page_category: str | None,
    top_k: int,
    exclude_source_tiers: list[str] | None = None,
) -> tuple[list[dict[str, Any]], float]:
    started = time.perf_counter()
    chunks = retriever.retrieve(
        query,
        top_k=top_k,
        page_category=page_category if page_category != "general" else None,
        exclude_source_tiers=exclude_source_tiers,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    return chunks, latency_ms


def evaluate_url_targets(
    chunks: list[dict[str, Any]],
    expected_patterns: list[str],
    forbidden_patterns: list[str],
    k: int = 3,
) -> bool:
    top = chunks[:k]
    urls = [str(c.get("source_url") or "").lower() for c in top]
    if forbidden_patterns and any(
        any(f.lower() in url for f in forbidden_patterns) for url in urls
    ):
        return False
    if not expected_patterns:
        return bool(urls)
    return any(
        any(p.lower() in url for p in expected_patterns) for url in urls
    )


def url_target_pass_rate(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    passed = sum(1 for row in rows if row.get("url_target_pass"))
    return passed / len(rows)


def evaluate_hits(
    chunks: list[dict[str, Any]],
    expected_keywords: list[str],
    k: int,
) -> bool:
    text = " ".join(str(c.get("chunk_text", "")) for c in chunks[:k]).lower()
    return any(keyword.lower() in text for keyword in expected_keywords)


def first_hit_rank(chunks: list[dict[str, Any]], expected_keywords: list[str]) -> int | None:
    for idx, chunk in enumerate(chunks, start=1):
        text = str(chunk.get("chunk_text", "")).lower()
        if any(keyword.lower() in text for keyword in expected_keywords):
            return idx
    return None


def metadata_ok(chunks: list[dict[str, Any]]) -> bool:
    required = ("source_url", "page_category", "chunk_id")
    return all(all(chunk.get(key) for key in required) for chunk in chunks)


def unique_sources(chunks: list[dict[str, Any]], k: int = 5) -> int:
    return len({str(chunk.get("source_url", "")) for chunk in chunks[:k] if chunk.get("source_url")})


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = max(len(rows), 1)
    vector_top1 = sum(1 for row in rows if row["vector"]["top1_hit"]) / total
    vector_top3 = sum(1 for row in rows if row["vector"]["top3_hit"]) / total
    vector_top5 = sum(1 for row in rows if row["vector"]["top5_hit"]) / total
    hybrid_top1 = sum(1 for row in rows if row["hybrid"]["top1_hit"]) / total
    hybrid_top3 = sum(1 for row in rows if row["hybrid"]["top3_hit"]) / total
    hybrid_top5 = sum(1 for row in rows if row["hybrid"]["top5_hit"]) / total
    vector_latency = statistics.mean(row["vector"]["latency_ms"] for row in rows)
    hybrid_latency = statistics.mean(row["hybrid"]["latency_ms"] for row in rows)
    improved = [row["id"] for row in rows if row["comparison"] == "improved"]
    regressed = [row["id"] for row in rows if row["comparison"] == "regressed"]
    same = [row["id"] for row in rows if row["comparison"] == "same"]
    metadata_loss = [
        row["id"]
        for row in rows
        if not row["vector"]["metadata_ok"] or not row["hybrid"]["metadata_ok"]
    ]
    fallback_count = sum(1 for row in rows if row["hybrid"]["fallback_to_vector_only"])
    exact_keyword_recall_delta = hybrid_top5 - vector_top5
    gates = {
        "top5_non_regression": hybrid_top5 >= vector_top5,
        "top3_non_regression": hybrid_top3 >= vector_top3,
        "improves_20_percent_exact_queries": len(improved) >= math.ceil(total * 0.20),
        "regressions_not_greater_than_improvements": len(regressed) <= len(improved),
        "latency_overhead_under_150ms": (hybrid_latency - vector_latency) <= 150.0,
        "metadata_preserved": not metadata_loss,
        "fallback_zero": fallback_count == 0,
    }
    recommendation = "enable hybrid in staging" if all(gates.values()) else "tune hybrid before staging"
    if not improved and not regressed:
        recommendation = "keep vector-only"

    return {
        "query_count": len(rows),
        "vector_top1_hit_rate": vector_top1,
        "vector_top3_hit_rate": vector_top3,
        "vector_top5_hit_rate": vector_top5,
        "hybrid_top1_hit_rate": hybrid_top1,
        "hybrid_top3_hit_rate": hybrid_top3,
        "hybrid_top5_hit_rate": hybrid_top5,
        "exact_keyword_recall_delta": exact_keyword_recall_delta,
        "average_latency_vector_ms": vector_latency,
        "average_latency_hybrid_ms": hybrid_latency,
        "hybrid_latency_overhead_ms": hybrid_latency - vector_latency,
        "average_source_diversity_vector_top5": statistics.mean(row["vector"]["source_diversity_top5"] for row in rows),
        "average_source_diversity_hybrid_top5": statistics.mean(row["hybrid"]["source_diversity_top5"] for row in rows),
        "queries_improved": improved,
        "queries_regressed": regressed,
        "queries_same": same,
        "fallback_count": fallback_count,
        "metadata_loss_queries": metadata_loss,
        "quality_gates": gates,
        "recommendation": recommendation,
    }


def classify_comparison(vector_rank: int | None, hybrid_rank: int | None) -> str:
    if vector_rank is None and hybrid_rank is None:
        return "same"
    if vector_rank is None:
        return "improved"
    if hybrid_rank is None:
        return "regressed"
    if hybrid_rank < vector_rank:
        return "improved"
    if hybrid_rank > vector_rank:
        return "regressed"
    return "same"


def run_comparison(args: argparse.Namespace) -> dict[str, Any]:
    query_path = Path(args.queries)
    result_path = Path(args.output)
    queries = load_queries(query_path)
    chunk_path = Path(args.chunks) if args.chunks else latest_chunk_snapshot()
    chunks = load_chunks(chunk_path)
    embedder = LocalTfidfEmbedder(chunks)
    store = LocalVectorStore(chunks, embedder)

    with tempfile.TemporaryDirectory(prefix="mobcoder-retrieval-eval-") as tmp:
        bm25_path = Path(tmp) / "bm25_index.pkl"
        build_bm25_index(chunks, bm25_path)
        vector_retriever = make_retriever(
            hybrid_enabled=False,
            store=store,
            embedder=embedder,
            bm25_index_path=bm25_path,
        )
        hybrid_retriever = make_retriever(
            hybrid_enabled=True,
            store=store,
            embedder=embedder,
            bm25_index_path=bm25_path,
        )
        rows: list[dict[str, Any]] = []
        for query_def in queries:
            expected = query_def.get("expected_keywords_any") or []
            expected_urls = query_def.get("expected_source_url_contains_any") or []
            forbidden_urls = query_def.get("forbidden_source_url_contains_any") or []
            category = query_def.get("expected_category")
            query = query_def["query"]
            plan = resolve_retrieval_plan(user_query=query)
            vector_chunks, vector_latency = run_retriever(
                vector_retriever,
                query=plan.search_query,
                page_category=plan.page_category or category,
                top_k=args.top_k,
                exclude_source_tiers=list(plan.exclude_source_tiers),
            )
            hybrid_chunks, hybrid_latency = run_retriever(
                hybrid_retriever,
                query=plan.search_query,
                page_category=plan.page_category or category,
                top_k=args.top_k,
                exclude_source_tiers=list(plan.exclude_source_tiers),
            )
            vector_rank = first_hit_rank(vector_chunks[:5], expected)
            hybrid_rank = first_hit_rank(hybrid_chunks[:5], expected)
            comparison = classify_comparison(vector_rank, hybrid_rank)
            url_pass = evaluate_url_targets(
                hybrid_chunks,
                expected_urls,
                forbidden_urls,
                k=3,
            )
            rows.append(
                {
                    "id": query_def["id"],
                    "query": query,
                    "expected_keywords_any": expected,
                    "expected_source_url_contains_any": expected_urls,
                    "forbidden_source_url_contains_any": forbidden_urls,
                    "expected_category": category,
                    "url_target_pass": url_pass,
                    "comparison": comparison,
                    "vector": summarize_mode(vector_chunks, expected, vector_latency),
                    "hybrid": summarize_mode(hybrid_chunks, expected, hybrid_latency),
                }
            )

    summary = summarize(rows)
    summary["url_target_pass_rate"] = url_target_pass_rate(rows)
    summary["url_target_gate_pass"] = summary["url_target_pass_rate"] >= args.url_gate
    result = {
        "mode": "offline_local_chunks",
        "chunk_snapshot": str(chunk_path),
        "query_set": str(query_path),
        "summary": summary,
        "rows": rows,
    }
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_report(result, result_path)
    return result


def summarize_mode(
    chunks: list[dict[str, Any]],
    expected: list[str],
    latency_ms: float,
) -> dict[str, Any]:
    return {
        "top1_hit": evaluate_hits(chunks, expected, 1),
        "top3_hit": evaluate_hits(chunks, expected, 3),
        "top5_hit": evaluate_hits(chunks, expected, 5),
        "first_hit_rank": first_hit_rank(chunks[:5], expected),
        "latency_ms": latency_ms,
        "source_diversity_top5": unique_sources(chunks, 5),
        "metadata_ok": metadata_ok(chunks),
        "fallback_to_vector_only": all("retrieval_paths" not in chunk for chunk in chunks),
        "top_sources": [
            {
                "chunk_id": chunk.get("chunk_id"),
                "source_url": chunk.get("source_url"),
                "page_title": chunk.get("page_title"),
                "page_category": chunk.get("page_category"),
                "retrieval_paths": chunk.get("retrieval_paths", ["vector"]),
                "score": chunk.get("score"),
                "rrf_score": chunk.get("rrf_score"),
                "bm25_score": chunk.get("bm25_score"),
            }
            for chunk in chunks[:5]
        ],
    }


def print_report(result: dict[str, Any], result_path: Path) -> None:
    summary = result["summary"]
    print("\nRetrieval comparison")
    print("====================")
    print(f"Query set: {result['query_set']}")
    print(f"Chunk snapshot: {result['chunk_snapshot']}")
    print(f"Queries: {summary['query_count']}")
    print(
        "Hit rates top1/top3/top5: "
        f"vector={summary['vector_top1_hit_rate']:.2%}/"
        f"{summary['vector_top3_hit_rate']:.2%}/"
        f"{summary['vector_top5_hit_rate']:.2%} "
        f"hybrid={summary['hybrid_top1_hit_rate']:.2%}/"
        f"{summary['hybrid_top3_hit_rate']:.2%}/"
        f"{summary['hybrid_top5_hit_rate']:.2%}"
    )
    print(
        "Latency avg: "
        f"vector={summary['average_latency_vector_ms']:.2f}ms "
        f"hybrid={summary['average_latency_hybrid_ms']:.2f}ms "
        f"overhead={summary['hybrid_latency_overhead_ms']:.2f}ms"
    )
    print(
        "Source diversity top5 avg: "
        f"vector={summary['average_source_diversity_vector_top5']:.2f} "
        f"hybrid={summary['average_source_diversity_hybrid_top5']:.2f}"
    )
    print(f"Improved: {len(summary['queries_improved'])} {summary['queries_improved']}")
    print(f"Regressed: {len(summary['queries_regressed'])} {summary['queries_regressed']}")
    print(f"Same: {len(summary['queries_same'])}")
    print(f"Fallback count: {summary['fallback_count']}")
    print(
        f"URL target pass rate (top-3): {summary.get('url_target_pass_rate', 0):.2%} "
        f"(gate {'PASS' if summary.get('url_target_gate_pass') else 'FAIL'})"
    )
    print(f"Recommendation: {summary['recommendation']}")
    print(f"Results written to: {result_path}")
    if summary.get("url_target_gate_pass") is False:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare vector-only and hybrid retrieval.")
    parser.add_argument("--compare-vector-hybrid", action="store_true", default=True)
    parser.add_argument("--queries", default=str(QUERY_PATH))
    parser.add_argument("--chunks", default="")
    parser.add_argument("--output", default=str(RESULT_PATH))
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--url-gate", type=float, default=0.85)
    args = parser.parse_args()
    get_settings()
    run_comparison(args)


if __name__ == "__main__":
    main()
