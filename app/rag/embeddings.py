from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import cast

from openai import APIStatusError, OpenAI, RateLimitError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.settings import get_settings

logger = logging.getLogger(__name__)
_RETRYABLE_ERRORS = (RateLimitError, APIStatusError)

# ---------------------------------------------------------------------------
# Embedding cache
# ---------------------------------------------------------------------------
# Single-query embeddings are cached by a SHA-256 hash of the (model, text)
# pair. This avoids redundant OpenAI API calls for repeated or identical
# queries (e.g. the same user asking the same thing in a new session, or
# quick-chip clicks that share a fixed prompt).
#
# maxsize=512 keeps memory bounded to ~512 × 1536 floats × 4 bytes ≈ 3 MB
# for text-embedding-3-small. Safe for production.
# ---------------------------------------------------------------------------

@lru_cache(maxsize=512)
def _cached_embed(model: str, text_hash: str, text: str) -> tuple[float, ...]:
    """Internal LRU-cached embedding call. Returns a tuple (hashable for lru_cache)."""
    settings = get_settings()
    client = OpenAI(api_key=settings.require_openai_key())
    response = client.embeddings.create(model=model, input=text)
    return tuple(cast(list[float], response.data[0].embedding))


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingClient:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = OpenAI(api_key=self.settings.require_openai_key())
        self.model = self.settings.openai_embedding_model

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_ERRORS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string, with LRU caching by content hash."""
        cleaned = text.strip().replace("\n", " ")[:8_000]
        h = _text_hash(cleaned)
        try:
            return list(_cached_embed(self.model, h, cleaned))
        except Exception:
            # Cache miss or API error — fall through to direct call
            response = self._client.embeddings.create(model=self.model, input=cleaned)
            return cast(list[float], response.data[0].embedding)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        cleaned = [t.strip().replace("\n", " ")[:8_000] for t in texts]
        batch_size = 256
        all_embeddings: list[list[float]] = []
        for i in range(0, len(cleaned), batch_size):
            batch = cleaned[i : i + batch_size]
            all_embeddings.extend(self._embed_single_batch(batch))
        return all_embeddings

    @retry(
        retry=retry_if_exception_type(_RETRYABLE_ERRORS),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _embed_single_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in sorted(response.data, key=lambda x: x.index)]


_client: EmbeddingClient | None = None


def get_embedding_client() -> EmbeddingClient:
    global _client
    if _client is None:
        _client = EmbeddingClient()
    return _client
