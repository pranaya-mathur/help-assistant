#!/usr/bin/env python3
"""Chunk scraped pages and embed into Chroma."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings  # noqa: E402
from app.crawler.chunker import chunk_pages  # noqa: E402
from app.crawler.page_loader import dict_to_scraped_page  # noqa: E402
from app.rag.retriever import get_retriever  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest")


def _pages_from_raw(raw: object) -> list:
    pages = []
    if not isinstance(raw, list) or not raw or not isinstance(raw[0], dict):
        return pages
    if "url" in raw[0] and "source_url" not in raw[0]:
        from app.crawler.page_loader import apify_item_to_page_dict

        for item in raw:
            d = apify_item_to_page_dict(item)
            if d:
                sp = dict_to_scraped_page(d)
                if sp:
                    pages.append(sp)
    else:
        for item in raw:
            sp = dict_to_scraped_page(item)
            if sp:
                pages.append(sp)
    return pages


def load_pages(path: Path) -> list:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return _pages_from_raw(raw)


def merge_supplemental_pages(pages: list) -> list:
    """Append supplemental knowledge into crawled pages (same URL) or add as new pages."""
    import hashlib

    supplemental_dir = ROOT / "data" / "formatted" / "supplemental"
    if not supplemental_dir.is_dir():
        return pages

    by_url = {p.source_url: p for p in pages}
    for supp_path in sorted(supplemental_dir.glob("*.json")):
        try:
            raw = json.loads(supp_path.read_text(encoding="utf-8"))
            items = raw if isinstance(raw, list) else [raw]
            for item in items:
                if not isinstance(item, dict):
                    continue
                sp = dict_to_scraped_page(item)
                if not sp:
                    continue
                merge_into = (item.get("merge_into_url") or sp.source_url).strip()
                extra = sp.clean_text.strip()
                if not extra:
                    continue
                title = (item.get("page_title") or sp.page_title or "Supplemental").strip()
                marker = f"\n\n--- Supplemental: {title} ---\n\n"
                if merge_into in by_url:
                    existing = by_url[merge_into]
                    if extra not in existing.clean_text:
                        existing.clean_text = existing.clean_text.rstrip() + marker + extra
                        existing.content_hash = hashlib.sha256(
                            existing.clean_text.strip().encode("utf-8")
                        ).hexdigest()[:16]
                    logger.info(
                        "Merged supplemental content into %s from %s",
                        merge_into,
                        supp_path.name,
                    )
                else:
                    pages.append(sp)
                    by_url[sp.source_url] = sp
                    logger.info(
                        "Added supplemental page %s from %s",
                        sp.source_url,
                        supp_path.name,
                    )
        except Exception as exc:
            logger.warning("Skipping supplemental file %s: %s", supp_path, exc)
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest pages into vector store")
    parser.add_argument(
        "--input",
        default=str(ROOT / "data" / "formatted" / "pages_latest.json"),
        help="Pages JSON (formatted or raw Apify)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reset-collection",
        action="store_true",
        help="Delete and recreate Chroma collection before ingest",
    )
    parser.add_argument(
        "--skip-bm25",
        action="store_true",
        help="Skip building the local BM25 lexical index",
    )
    parser.add_argument(
        "--rebuild-bm25",
        action="store_true",
        help="Build the BM25 index from the generated chunks (default unless --skip-bm25)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input not found: {input_path}. Run scripts/crawl_mobcoder.py first.")
        sys.exit(1)

    pages = merge_supplemental_pages(load_pages(input_path))
    logger.info(f"Loaded {len(pages)} pages from {input_path} (with supplemental merge)")

    chunks = chunk_pages(pages, deduplicate=True)
    logger.info(f"Produced {len(chunks)} chunks")

    if args.dry_run:
        print(f"Dry run: would ingest {len(chunks)} chunks")
        return

    settings = get_settings()
    chunks_dir = Path(settings.chunks_data_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    snapshot = chunks_dir / f"chunks_{ts}.json"
    with open(snapshot, "w", encoding="utf-8") as f:
        json.dump([c.model_dump() for c in chunks], f, ensure_ascii=False, indent=2)

    if args.reset_collection:
        from app.rag.vector_store import reset_vector_store

        reset_vector_store()
        import app.rag.retriever as retriever_mod

        retriever_mod._retriever = None
        logger.info("Chroma collection reset; ingesting fresh chunks.")

    retriever = get_retriever()
    ingested = retriever.ingest_chunks(
        chunks,
        skip_existing=not args.reset_collection,
    )
    bm25_indexed = 0
    bm25_index_path = settings.bm25_index_path
    if not args.skip_bm25:
        from app.rag.bm25_index import build_bm25_index, reset_bm25_index_cache

        bm25_index = build_bm25_index(chunks, bm25_index_path)
        reset_bm25_index_cache()
        bm25_indexed = bm25_index.doc_count
        logger.info("BM25 index ready at %s (%d documents)", bm25_index_path, bm25_indexed)

    manifest_path = Path(settings.ingest_manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest.update({
        "last_ingest_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "chunks": len(chunks),
        "ingested_new": ingested,
        "pages": len(pages),
        "snapshot": str(snapshot),
        "input": str(input_path),
        "bm25_index_path": bm25_index_path if not args.skip_bm25 else "",
        "bm25_indexed": bm25_indexed,
    })
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(
        f"\nIngest complete: {ingested} new chunks "
        f"(snapshot: {snapshot}, bm25_indexed: {bm25_indexed})\n"
    )


if __name__ == "__main__":
    main()
