from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from app.crawler.schema import ScrapedPage, SourceTier, url_is_official

_ERROR_PAGE_RE = re.compile(r"\b404\b.{0,40}(page )?not found", re.I)

_GEO_SEO_PATH_RE = re.compile(
    r"(best-.*-company-in-|top-.*-company-in-|"
    r".*-development-company-in-|"
    r".*-app-development-company-in-)",
    re.I,
)


def _is_error_page(title: str, text: str) -> bool:
    """True for crawled error/placeholder pages (e.g. 404s) that shouldn't become chunks."""
    return bool(_ERROR_PAGE_RE.search(f"{title} {text[:200]}"))


def is_geo_seo_url(url: str) -> bool:
    """City/SEO landing pages that pollute ai_agents retrieval if mis-tagged."""
    path = urlparse(url).path.lower().strip("/")
    if not path:
        return False
    return bool(_GEO_SEO_PATH_RE.search(path))


def infer_source_tier(url: str, page_category: str = "") -> SourceTier:
    if is_geo_seo_url(url):
        return "seo_geo"
    path = urlparse(url).path.lower()
    if any(k in path for k in ("/contact", "/career", "/privacy", "/terms", "/legal")):
        return "utility"
    if any(
        k in path
        for k in (
            "/service",
            "/ai",
            "/case-stud",
            "/about",
            "/solution",
            "/agent",
            "/portfolio",
        )
    ):
        return "core"
    if page_category in ("services", "ai_agents", "case_studies", "about"):
        return "core"
    if page_category in ("contact", "careers"):
        return "utility"
    return "core"


def infer_page_category(url: str, title: str = "") -> str:
    path = urlparse(url).path.lower()
    combined = f"{path} {title.lower()}"
    if is_geo_seo_url(url):
        return "general"
    # Path-based checks first — avoid substring traps like "work" inside "frameworks".
    if "/blog/" in path or path.startswith("blog/"):
        return "blog"
    if "/case-stud" in path or "/portfolio" in path:
        return "case_studies"
    if re.search(r"\b(case[- ]stud(y|ies)|portfolio)\b", combined):
        return "case_studies"
    if re.search(r"\b(work|our work)\b", combined) and "/blog/" not in path:
        return "case_studies"
    if any(k in combined for k in ("blog", "news", "insight")):
        return "blog"
    if any(k in combined for k in ("ai", "agent", "ml", "machine-learning", "genai")):
        return "ai_agents"
    if any(k in combined for k in ("service", "solution", "offering")):
        return "services"
    if any(k in combined for k in ("pricing", "price")):
        return "pricing"
    if any(k in combined for k in ("about", "team", "who-we")):
        return "about"
    if any(k in combined for k in ("contact", "get-in-touch")):
        return "contact"
    if any(k in combined for k in ("career", "job")):
        return "careers"
    return "general"


def apify_item_to_page_dict(item: dict[str, Any]) -> dict[str, Any] | None:
    url = (item.get("url") or item.get("canonicalUrl") or "").strip()
    if not url or not url_is_official(url):
        return None

    title = (item.get("title") or "").strip()
    text = (item.get("text") or item.get("markdown") or "").strip()
    if not text or _is_error_page(title, text):
        return None

    category = infer_page_category(url, title)
    return {
        "source_url": url,
        "page_title": title,
        "page_category": category,
        "source_tier": infer_source_tier(url, category),
        "clean_text": text[:30_000],
        "raw_text": (item.get("html") or "")[:50_000],
        "last_modified": (item.get("metadata") or {}).get("lastModified"),
    }


def dict_to_scraped_page(data: dict[str, Any]) -> ScrapedPage | None:
    url = data.get("source_url", "")
    if not url or not url_is_official(url):
        return None
    if _is_error_page(data.get("page_title", ""), data.get("clean_text", "")):
        return None
    try:
        category = infer_page_category(url, data.get("page_title", ""))
        tier = infer_source_tier(url, category)
        return ScrapedPage(
            id=data.get("id", ""),
            source_url=url,
            page_title=data.get("page_title", ""),
            page_category=category,  # type: ignore[arg-type]
            source_tier=tier,  # type: ignore[arg-type]
            section=data.get("section", ""),
            content_type=data.get("content_type", "general"),  # type: ignore[arg-type]
            clean_text=data.get("clean_text", ""),
            raw_text=data.get("raw_text", ""),
            last_modified=data.get("last_modified"),
        )
    except Exception:
        return None
