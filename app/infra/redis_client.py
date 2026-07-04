from __future__ import annotations

import threading
from typing import Any

_clients: dict[str, Any] = {}
_lock = threading.Lock()


def get_redis_client(redis_url: str):
    """Return a shared Redis client for the given URL."""
    if not redis_url:
        raise RuntimeError("REDIS_URL is required for Redis-backed features")

    with _lock:
        client = _clients.get(redis_url)
        if client is None:
            try:
                import redis
            except ImportError as exc:
                raise RuntimeError(
                    "Redis backend requires the 'redis' package. "
                    "Install with: pip install redis"
                ) from exc
            client = redis.from_url(redis_url, decode_responses=True)
            _clients[redis_url] = client
        return client


def reset_redis_clients_for_tests() -> None:
    with _lock:
        _clients.clear()


def verify_redis_connectivity(redis_url: str) -> None:
    """Ping Redis at startup; raises with actionable message on failure."""
    client = get_redis_client(redis_url)
    if not client.ping():
        raise RuntimeError(f"Redis ping failed for REDIS_URL={redis_url!r}")
