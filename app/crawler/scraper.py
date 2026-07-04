from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from apify_client import ApifyClient
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import get_settings
from app.crawler.page_loader import infer_page_category
from app.crawler.schema import ALLOWED_DOMAINS, ImportantLink, ScrapedPage, url_is_official

logger = logging.getLogger(__name__)

SEED_URLS: list[str] = [
    "https://mobcoder.ai/",
    "https://mobcoder.ai/services",
    "https://mobcoder.ai/services/agentic-ai",
    "https://mobcoder.ai/about",
    "https://mobcoder.ai/contact-us",
    "https://mobcoder.ai/case-studies",
    "https://mobcoder.ai/blog",
    "https://mobcoder.ai/ai-development-services",
    "https://mobcoder.ai/generative-ai-development-services",
    "https://mobcoder.ai/fintech-ai-development-services",
]

_URL_CATEGORY_MAP: list[tuple[str, str]] = [
    ("case-stud", "case_studies"),
    ("portfolio", "case_studies"),
    ("agent", "ai_agents"),
    ("/ai", "ai_agents"),
    ("machine-learning", "ai_agents"),
    ("service", "services"),
    ("solution", "services"),
    ("about", "about"),
    ("contact", "contact"),
    ("career", "careers"),
    ("blog", "blog"),
]


def _infer_page_category(url: str) -> str:
    url_lower = url.lower()
    for fragment, category in _URL_CATEGORY_MAP:
        if fragment in url_lower:
            return category
    return infer_page_category(url)


class _RobotsCache:
    def __init__(self) -> None:
        self._cache: dict[str, RobotFileParser] = {}

    def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        try:
            parsed = urlparse(url)
            domain_key = f"{parsed.scheme}://{parsed.netloc}"
            if domain_key not in self._cache:
                rp = RobotFileParser()
                rp.set_url(f"{domain_key}/robots.txt")
                try:
                    rp.read()
                except Exception:
                    rp = RobotFileParser()
                self._cache[domain_key] = rp
            return self._cache[domain_key].can_fetch(user_agent, url)
        except Exception:
            return True


_robots = _RobotsCache()


def _valid_http_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


class ApifyScraper:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.settings.require_apify_token()
        self.client = ApifyClient(self.settings.apify_api_token)

    @staticmethod
    def _get_dataset_id(run: Any) -> str:
        if isinstance(run, dict):
            did = run.get("defaultDatasetId") or run.get("default_dataset_id")
        else:
            did = (
                getattr(run, "default_dataset_id", None)
                or getattr(run, "defaultDatasetId", None)
            )
        if not did:
            raise RuntimeError(f"Cannot find defaultDatasetId in Apify run: {run!r}")
        return str(did)

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _call_actor(self, actor_input: dict[str, Any]) -> Any:
        run = self.client.actor(self.settings.apify_actor_id).call(run_input=actor_input)
        if run is None:
            raise RuntimeError("Apify actor .call() returned None.")
        self._get_dataset_id(run)
        return run

    def _run_actor(
        self,
        start_urls: list[str],
        max_crawl_pages: int | None = None,
        max_crawl_depth: int | None = None,
    ) -> list[dict[str, Any]]:
        allowed_urls = [
            u for u in start_urls if _valid_http_url(u) and url_is_official(u)
        ]
        if not allowed_urls:
            logger.error("No URLs passed the domain whitelist.")
            return []

        # Use playwright (headless Chromium) so JS-rendered pages (case studies,
        # pricing, service sub-pages built in React/Next.js) are fully rendered
        # before text extraction. cheerio only parses raw HTML and misses content
        # that requires JavaScript execution.
        actor_input: dict[str, Any] = {
            "startUrls": [{"url": u} for u in allowed_urls],
            "crawlerType": "playwright:chrome",
            "includeUrlGlobs": [
                {"glob": f"https://{domain}/**"}
                for domain in self.settings.allowed_domains_list()
            ] or [
                {"glob": "https://mobcoder.ai/**"},
                {"glob": "https://www.mobcoder.ai/**"},
            ],
            "excludeUrlGlobs": [
                {"glob": "**/*.pdf"},
                {"glob": "**/*.doc*"},
                {"glob": "**/*.zip"},
                {"glob": "**/wp-admin/**"},
                {"glob": "**/wp-login/**"},
                {"glob": "**/privacy-policy**"},
                {"glob": "**#**"},
            ],
            "readableTextCharThreshold": 100,
            "removeCookieWarnings": True,
            # Wait for the page to be fully rendered before extracting content
            "waitForSelectorOnPage": "body",
            "proxyConfiguration": {"useApifyProxy": True},
        }

        if max_crawl_pages is not None:
            actor_input["maxCrawlPages"] = max_crawl_pages
        if max_crawl_depth is not None:
            actor_input["maxCrawlDepth"] = max_crawl_depth

        logger.info(
            f"Starting Apify actor '{self.settings.apify_actor_id}' "
            f"with {len(allowed_urls)} seed(s)."
        )
        run = self._call_actor(actor_input)
        dataset_id = self._get_dataset_id(run)

        results: list[dict[str, Any]] = []
        for item in self.client.dataset(dataset_id).iterate_items():
            results.append(item)

        logger.info(f"Apify crawl complete. {len(results)} pages.")
        return results

    def _apify_item_to_page(self, item: dict[str, Any]) -> ScrapedPage | None:
        url = (item.get("url") or item.get("canonicalUrl") or "").strip()
        if not _valid_http_url(url) or not url_is_official(url):
            return None
        # Apify website-content-crawler enforces robots.txt; skip duplicate pre-check
        # (mobcoder.ai often disallows "*" while still allowing the Apify actor).

        title = (item.get("title") or "").strip()
        text = (item.get("text") or item.get("markdown") or "").strip()
        raw_html = (item.get("html") or "")[:50_000]

        links: list[ImportantLink] = []
        for link in (item.get("links") or [])[:50]:
            href = (link.get("href") or "").strip()
            label = (link.get("text") or "").strip()
            if href and label and url_is_official(href):
                try:
                    links.append(ImportantLink(label=label[:200], url=href))
                except Exception:
                    pass

        raw_meta = item.get("metadata") or {}
        last_modified = raw_meta.get("lastModified")

        try:
            return ScrapedPage(
                source_url=url,
                page_title=title,
                page_category=_infer_page_category(url),  # type: ignore[arg-type]
                raw_text=raw_html,
                clean_text=text[:30_000],
                important_links=links,
                last_modified=last_modified,
            )
        except Exception as exc:
            logger.warning(f"Could not build ScrapedPage for {url}: {exc}")
            return None

    def scrape_urls(
        self,
        urls: list[str] | None = None,
        max_pages_per_seed: int | None = None,
        save_raw: bool = True,
    ) -> list[ScrapedPage]:
        seed = urls or SEED_URLS
        max_pages = max_pages_per_seed or self.settings.crawl_max_pages_per_seed
        raw_results = self._run_actor(
            seed,
            max_crawl_pages=max_pages,
            max_crawl_depth=self.settings.crawl_max_depth,
        )

        pages: list[ScrapedPage] = []
        seen_ids: set[str] = set()
        for item in raw_results:
            page = self._apify_item_to_page(item)
            if page is None or not page.clean_text.strip():
                continue
            if page.id in seen_ids:
                continue
            seen_ids.add(page.id)
            pages.append(page)

        if save_raw:
            self._save_raw(raw_results)

        logger.info(f"Scrape complete: {len(pages)} valid pages.")
        return pages

    def _save_raw(self, raw_results: list[dict[str, Any]]) -> Path:
        raw_dir = Path(self.settings.raw_data_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        out_file = raw_dir / f"apify_raw_{ts}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(raw_results, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved raw results -> {out_file}")
        return out_file
