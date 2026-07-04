from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

_GENERIC_QUERY_RE = re.compile(
    r"^(hi|hello|hey|thanks|thank you|tell me more|more info|what is this|"
    r"what's this|help|ok|okay|yes|no|sure)\W*$",
    re.I,
)

_GENERIC_SITE_TITLE_RE = re.compile(
    r"^(mobcoder(\s*ai)?|home|welcome)\b",
    re.I,
)


def normalize_page_url(url: str) -> str:
    """Strip query/fragment, lowercase host, drop www., trim trailing slash."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        if not parsed.scheme or not parsed.netloc:
            return raw.rstrip("/")
        host = parsed.netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/") or ""
        return urlunparse((parsed.scheme.lower(), host, path, "", "", ""))
    except Exception:
        return raw.rstrip("/")


def page_slug(url: str) -> str:
    """Last path segment for query augmentation (e.g. fintech-wallet)."""
    normalized = normalize_page_url(url)
    if not normalized:
        return ""
    try:
        path = urlparse(normalized).path.strip("/")
        if not path:
            return ""
        slug = path.split("/")[-1]
        return slug.replace("-", " ").strip()
    except Exception:
        return ""


def urls_match(a: str, b: str) -> bool:
    """Normalized URL equality."""
    na = normalize_page_url(a)
    nb = normalize_page_url(b)
    if not na or not nb:
        return False
    return na == nb


def is_generic_query(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if len(q) < 40 and _GENERIC_QUERY_RE.match(q):
        return True
    return False


def is_specific_page_title(title: str) -> bool:
    """True when document.title is more specific than a generic site label."""
    t = (title or "").strip()
    if not t or len(t) < 4:
        return False
    if _GENERIC_SITE_TITLE_RE.match(t) and len(t) < 30:
        return False
    return True


def slug_to_chip_hint(slug_text: str) -> str:
    """Turn a URL slug into a readable chip hint."""
    s = (slug_text or "").strip()
    if not s or len(s) < 3:
        return ""
    return s.title()
