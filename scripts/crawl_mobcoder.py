#!/usr/bin/env python3
"""Crawl mobcoder.ai via Apify and save raw JSON + formatted pages."""
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
from app.crawler.scraper import ApifyScraper, SEED_URLS  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("crawl_mobcoder")


def _load_sitemap_seeds() -> list[str]:
    from scripts.discover_urls import fetch_sitemap_urls, merge_seed_urls

    return merge_seed_urls(fetch_sitemap_urls())


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl mobcoder.ai with Apify")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Max pages for Apify crawl (default: CRAWL_MAX_PAGES_PER_SEED or 500 with --use-sitemap)",
    )
    parser.add_argument("--seeds", nargs="*", default=None, help="Override seed URLs")
    parser.add_argument(
        "--use-sitemap",
        action="store_true",
        help="Merge all sitemap URLs with SEED_URLS as start URLs",
    )
    args = parser.parse_args()

    if args.seeds:
        seed_urls = args.seeds
    elif args.use_sitemap:
        seed_urls = _load_sitemap_seeds()
        logger.info("Using %s seed URLs (sitemap + defaults)", len(seed_urls))
    else:
        seed_urls = SEED_URLS

    settings = get_settings()
    max_pages = args.max_pages
    if max_pages is None and args.use_sitemap:
        max_pages = max(settings.crawl_max_pages_per_seed, 500)

    scraper = ApifyScraper()
    pages = scraper.scrape_urls(
        urls=seed_urls,
        max_pages_per_seed=max_pages,
        save_raw=True,
    )

    formatted_dir = ROOT / "data" / "formatted"
    formatted_dir.mkdir(parents=True, exist_ok=True)
    out = formatted_dir / "pages_latest.json"
    payload = [p.model_dump() for p in pages]
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    manifest_path = Path(settings.ingest_manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    manifest["last_crawl_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["pages_crawled"] = len(pages)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nCrawl complete: {len(pages)} pages → {out}\n")


if __name__ == "__main__":
    main()
