from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

PageCategory = Literal[
    "services",
    "ai_agents",
    "case_studies",
    "about",
    "contact",
    "careers",
    "blog",
    "general",
]

ContentType = Literal[
    "overview",
    "capabilities",
    "case_study",
    "pricing",
    "process",
    "faq",
    "contact",
    "general",
]

SourceTier = Literal["core", "seo_geo", "utility"]

ALLOWED_DOMAINS: frozenset[str] = frozenset(["mobcoder.ai", "www.mobcoder.ai"])


def url_is_official(url: str) -> bool:
    try:
        netloc = urlparse(url).netloc.lower()
        return any(netloc == d or netloc.endswith("." + d) for d in ALLOWED_DOMAINS)
    except Exception:
        return False


class ImportantLink(BaseModel):
    label: str
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_official(cls, v: str) -> str:
        if v and not url_is_official(v):
            raise ValueError(f"Link URL not from allowed domain: {v}")
        return v


class PageMetadata(BaseModel):
    official_source: bool = True
    requires_citation: bool = True
    source_freshness: Optional[str] = None


class ScrapedPage(BaseModel):
    id: str = Field(default="")
    source_url: str
    domain: str = ""
    page_title: str = ""
    page_category: PageCategory = "general"
    source_tier: SourceTier = "core"
    section: str = ""
    content_type: ContentType = "general"
    language: str = "en"
    last_scraped_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_modified: Optional[str] = None
    raw_text: str = ""
    clean_text: str = ""
    content_hash: str = ""
    important_links: list[ImportantLink] = Field(default_factory=list)
    metadata: PageMetadata = Field(default_factory=PageMetadata)

    @field_validator("source_url")
    @classmethod
    def source_must_be_official(cls, v: str) -> str:
        if not url_is_official(v):
            raise ValueError(f"source_url must be from mobcoder.ai. Got: {v}")
        return v

    def model_post_init(self, __context: object) -> None:
        if not self.id:
            # URL alone collides for bundled seed pages (same path, different topics).
            key = f"{self.source_url}|{self.page_title}|{self.content_hash or self.clean_text[:200]}"
            self.id = hashlib.md5(key.encode()).hexdigest()[:12]
        if not self.domain:
            self.domain = urlparse(self.source_url).netloc
        if not self.content_hash and self.clean_text:
            self.content_hash = hashlib.sha256(
                self.clean_text.strip().encode("utf-8")
            ).hexdigest()[:16]
        self.metadata.source_freshness = self.last_modified or self.last_scraped_at


class ChunkMetadata(BaseModel):
    official_source: bool = True
    requires_citation: bool = True
    source_freshness: Optional[str] = None


class PageChunk(BaseModel):
    chunk_id: str
    page_id: str
    source_url: str
    page_title: str
    page_category: PageCategory
    source_tier: SourceTier = "core"
    content_type: ContentType
    section: str
    chunk_text: str
    token_count: int
    content_hash: str = ""
    embedding_text: str = ""
    citation_text: str = ""
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)

    def model_post_init(self, __context: object) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha256(
                self.chunk_text.strip().encode("utf-8")
            ).hexdigest()[:16]

    def build_citation_text(self) -> str:
        return (
            f"Source: {self.page_title} | "
            f"Category: {self.page_category} | "
            f"URL: {self.source_url}"
        )

    def build_embedding_text(self) -> str:
        parts: list[str] = []
        if self.page_title:
            parts.append(f"Page: {self.page_title}")
        if self.page_category:
            parts.append(f"Category: {self.page_category.replace('_', ' ').title()}")
        if self.section:
            parts.append(f"Section: {self.section}")
        parts.append(self.chunk_text)
        return "\n".join(parts)
