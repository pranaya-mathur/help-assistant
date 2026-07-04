from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections import defaultdict
from threading import Lock

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimiterBackend(ABC):
    @abstractmethod
    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        """Return (allowed, remaining requests in window after this decision)."""

    def allow(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        allowed, _ = self.check(key, limit, window_seconds)
        return allowed


class MemoryRateLimiterBackend(RateLimiterBackend):
    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        now = time.time()
        window_start = now - window_seconds
        with self._lock:
            times = [t for t in self._hits[key] if t >= window_start]
            if len(times) >= limit:
                self._hits[key] = times
                return False, 0
            times.append(now)
            self._hits[key] = times
            return True, max(0, limit - len(times))


class RedisRateLimiterBackend(RateLimiterBackend):
    """Distributed sliding-window limiter using Redis sorted sets."""

    def __init__(self, redis_url: str | None) -> None:
        if not redis_url:
            raise RuntimeError("RATE_LIMIT_BACKEND=redis requires REDIS_URL")
        from app.infra.redis_client import get_redis_client

        self._redis = get_redis_client(redis_url)
        self._prefix = "mobcoder:ratelimit:"

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> tuple[bool, int]:
        now = time.time()
        window_start = now - window_seconds
        redis_key = f"{self._prefix}{key}"
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, int(window_seconds) + 1)
        _, _, count, _ = pipe.execute()
        if count > limit:
            self._redis.zrem(redis_key, str(now))
            return False, 0
        return True, max(0, limit - int(count))


def _make_backend(backend: str, redis_url: str | None) -> RateLimiterBackend:
    normalized = (backend or "memory").strip().lower()
    if normalized == "memory":
        return MemoryRateLimiterBackend()
    if normalized == "redis":
        return RedisRateLimiterBackend(redis_url)
    raise RuntimeError(f"Unsupported RATE_LIMIT_BACKEND={backend!r}; use memory or redis")


class RateLimitMiddleware(BaseHTTPMiddleware):
    _WINDOW_SECONDS = 60.0

    def __init__(
        self,
        app,
        requests_per_minute: int = 30,
        *,
        chat_requests_per_minute: int | None = None,
        events_requests_per_minute: int | None = None,
        escalate_requests_per_minute: int | None = None,
        feedback_requests_per_minute: int | None = None,
        enabled: bool = True,
        trust_proxy_headers: bool = False,
        backend: str = "memory",
        redis_url: str | None = None,
    ) -> None:
        super().__init__(app)
        self._rpm = max(1, requests_per_minute)
        self._chat_rpm = max(1, chat_requests_per_minute or requests_per_minute)
        self._events_rpm = max(1, events_requests_per_minute or requests_per_minute)
        self._escalate_rpm = max(1, escalate_requests_per_minute or requests_per_minute)
        self._feedback_rpm = max(1, feedback_requests_per_minute or requests_per_minute)
        self._enabled = enabled
        self._trust_proxy_headers = trust_proxy_headers
        self._backend = _make_backend(backend, redis_url)

    def _limit_for_path(self, path: str) -> int:
        if path.startswith("/api/v1/chat"):
            return self._chat_rpm
        if path.startswith("/api/v1/events"):
            return self._events_rpm
        if path.startswith("/api/v1/escalate"):
            return self._escalate_rpm
        if path.startswith("/api/v1/feedback"):
            return self._feedback_rpm
        return self._rpm

    def _rate_limit_headers(self, limit: int, remaining: int) -> dict[str, str]:
        return {
            "X-RateLimit-Limit": str(limit),
            "X-RateLimit-Remaining": str(max(0, remaining)),
        }

    def _client_key(self, request: Request) -> str:
        if self._trust_proxy_headers:
            # Only enable behind an ingress that overwrites these headers.
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                return forwarded.split(",")[0].strip()
            real_ip = request.headers.get("x-real-ip")
            if real_ip:
                return real_ip.strip()
        if request.client:
            return request.client.host
        return "unknown"

    # Endpoints subject to per-IP rate limiting.
    # /feedback/stats and GET /feedback are internal (keyed by api key), still rate-limited.
    _RATE_LIMITED_PREFIXES = (
        "/api/v1/chat",
        "/api/v1/events",
        "/api/v1/feedback",
        "/api/v1/escalate",
    )

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path
        if not self._enabled or not any(path.startswith(p) for p in self._RATE_LIMITED_PREFIXES):
            return await call_next(request)

        limit = self._limit_for_path(path)
        key = self._client_key(request)
        allowed, remaining = self._backend.check(key, limit, self._WINDOW_SECONDS)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded ({limit} requests per minute). Please try again shortly.",
                },
                headers={
                    **self._rate_limit_headers(limit, 0),
                    "Retry-After": str(int(self._WINDOW_SECONDS)),
                },
            )

        response = await call_next(request)
        for header, value in self._rate_limit_headers(limit, remaining).items():
            response.headers[header] = value
        return response
