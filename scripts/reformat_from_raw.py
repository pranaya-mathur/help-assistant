#!/usr/bin/env python3
"""Re-process latest apify_raw_*.json into pages_latest.json without re-crawling."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawler.scraper import ApifyScraper  # noqa: E402


def main() -> None:
    raw_dir = ROOT / "data" / "raw"
    files = sorted(raw_dir.glob("apify_raw_*.json"))
    if not files:
        print("No apify_raw_*.json found. Run crawl_mobcoder.py first.")
        sys.exit(1)

    raw_path = files[-1]
    items = json.loads(raw_path.read_text(encoding="utf-8"))
    scraper = ApifyScraper()

    pages = []
    seen: set[str] = set()
    for item in items:
        page = scraper._apify_item_to_page(item)
        if page is None or not page.clean_text.strip():
            continue
        if page.id in seen:
            continue
        seen.add(page.id)
        pages.append(page)

    out = ROOT / "data" / "formatted" / "pages_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump([p.model_dump() for p in pages], f, ensure_ascii=False, indent=2)

    print(f"Reformatted {len(pages)} pages from {raw_path.name} → {out}")


if __name__ == "__main__":
    main()
