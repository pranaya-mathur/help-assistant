#!/usr/bin/env python3
"""Discover mobcoder.ai URLs from sitemap and merge with SEED_URLS."""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.crawler.scraper import SEED_URLS  # noqa: E402
from app.crawler.schema import url_is_official

SITEMAP_CANDIDATES = [
    "https://mobcoder.ai/sitemap.xml",
    "https://www.mobcoder.ai/sitemap.xml",
    "https://mobcoder.ai/sitemap_index.xml",
]

_LOC_RE = re.compile(r"<loc>\s*([^<]+)\s*</loc>", re.I)

# Sitemap entries that redirect to another official page (content covered by target URL)
REDIRECT_CANONICAL: dict[str, str] = {
    "https://mobcoder.ai/services/generative-ai": (
        "https://mobcoder.ai/generative-ai-development-services"
    ),
}


def _parse_locs(xml_text: str) -> list[str]:
    urls: list[str] = []
    try:
        root = ET.fromstring(xml_text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:loc", ns):
            if loc.text:
                urls.append(loc.text.strip())
        for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
            if loc.text:
                urls.append(loc.text.strip())
    except ET.ParseError:
        urls = _LOC_RE.findall(xml_text)
    return urls


def fetch_sitemap_urls(timeout: float = 20.0) -> list[str]:
    found: list[str] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for sm_url in SITEMAP_CANDIDATES:
            try:
                resp = client.get(sm_url)
                if resp.status_code != 200:
                    continue
                text = resp.text
                locs = _parse_locs(text)
                if not locs and "sitemapindex" in text.lower():
                    for sub in locs:
                        try:
                            r2 = client.get(sub)
                            if r2.status_code == 200:
                                found.extend(_parse_locs(r2.text))
                        except Exception:
                            pass
                found.extend(locs)
            except Exception:
                continue
    official = []
    seen: set[str] = set()
    for u in found:
        u = u.strip().rstrip("/")
        if not u or u in seen:
            continue
        if url_is_official(u):
            seen.add(u)
            official.append(u)
    return sorted(official)


def merge_seed_urls(extra: list[str] | None = None) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for u in list(SEED_URLS) + (extra or []):
        u = u.strip().rstrip("/")
        if u and u not in seen and url_is_official(u):
            seen.add(u)
            merged.append(u)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover crawl seeds from sitemap")
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "formatted" / "discovered_urls.json"),
    )
    parser.add_argument(
        "--crawled",
        default=str(ROOT / "data" / "formatted" / "pages_latest.json"),
        help="Optional crawled pages JSON to compute missing URLs",
    )
    args = parser.parse_args()

    sitemap_urls = fetch_sitemap_urls()
    seeds = merge_seed_urls(sitemap_urls)

    crawled: set[str] = set()
    crawled_path = Path(args.crawled)
    if crawled_path.exists():
        data = json.loads(crawled_path.read_text(encoding="utf-8"))
        for p in data:
            crawled.add((p.get("source_url") or "").rstrip("/"))

    def _is_crawled(url: str) -> bool:
        u = url.rstrip("/")
        if u in crawled:
            return True
        canonical = REDIRECT_CANONICAL.get(url) or REDIRECT_CANONICAL.get(u)
        if canonical and canonical.rstrip("/") in crawled:
            return True
        return False

    missing = [u for u in sitemap_urls if not _is_crawled(u)]

    out = {
        "sitemap_url_count": len(sitemap_urls),
        "seed_count": len(seeds),
        "crawled_count": len(crawled),
        "missing_from_crawl": missing,
        "seeds": seeds,
        "sitemap_urls": sitemap_urls,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    audit_path = ROOT / "data" / "formatted" / "url_audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "crawled_count": len(crawled),
                "sitemap_count": len(sitemap_urls),
                "missing_urls": missing,
                "blog_missing": [u for u in missing if "/blog/" in u],
                "service_missing": [u for u in missing if "/services/" in u],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Sitemap URLs: {len(sitemap_urls)}")
    print(f"Missing from last crawl: {len(missing)}")
    print(f"Merged seeds: {len(seeds)}")
    print(f"Wrote {out_path}")
    print(f"Wrote {audit_path}")


if __name__ == "__main__":
    main()
