#!/usr/bin/env python3
"""Automated P0 staging smoke checks (Redis session, rate limit, async CRM).

Run against a live API or in-process with FakeRedis:

  # In-process (no Docker Redis required):
  python3 scripts/staging_smoke.py

  # Against running staging API:
  STAGING_API_URL=http://127.0.0.1:8001 python3 scripts/staging_smoke.py --live

Exit 0 when all checks pass.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("APIFY_API_TOKEN", "test")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    line = f"[{status}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def smoke_redis_session() -> bool:
    from app.infra.redis_client import reset_redis_clients_for_tests
    from app.sessions.store import RedisSessionStore, reset_session_store_for_tests
    from tests.test_p0_production import FakeRedis

    fake = FakeRedis()
    reset_session_store_for_tests()
    reset_redis_clients_for_tests()

    import app.infra.redis_client as rc

    original = rc.get_redis_client
    rc.get_redis_client = lambda _url: fake  # type: ignore[assignment]

    try:
        store = RedisSessionStore("redis://fake/0")
        sid = store.create_session()
        store.save_turn(sid, "hello", "hi there", {"email": "a@b.com", "project_type": "ai_agent"})
        loaded = store.get(sid)
        ok = loaded is not None and loaded.lead_profile.get("email") == "a@b.com"
        ttl_ok = fake._expiries.get(f"mobcoder:session:{sid}", 0) > 0
        return _check("redis_session_persistence", ok) and _check("redis_session_ttl", ttl_ok)
    finally:
        rc.get_redis_client = original
        reset_session_store_for_tests()
        reset_redis_clients_for_tests()


def smoke_redis_rate_limit() -> bool:
    from app.middleware.rate_limit import RedisRateLimiterBackend
    from tests.test_p0_production import FakeRedis

    fake = FakeRedis()
    import app.infra.redis_client as rc

    rc.get_redis_client = lambda _url: fake  # type: ignore[assignment]
    backend = RedisRateLimiterBackend("redis://fake/0")
    first = backend.allow("10.0.0.1", 1)
    second = backend.allow("10.0.0.1", 1)
    return _check("redis_rate_limit_allow", first) and _check("redis_rate_limit_block", not second)


def smoke_async_crm_non_blocking() -> bool:
    from app.integrations import hubspot

    gate = threading.Event()
    release = threading.Event()

    class _Settings:
        hubspot_webhook_url = "https://example.test/hook"

    def slow_notify(*_a, **_k):
        gate.set()
        release.wait(timeout=5)
        return False

    hubspot.get_settings = lambda: _Settings()  # type: ignore[method-assign]
    hubspot.notify_qualified_lead = slow_notify  # type: ignore[method-assign]

    start = time.perf_counter()
    hubspot.dispatch_qualified_lead_async(
        lead_profile={"email": "x@y.com"},
        intent="sales",
        session_id="s-smoke",
        lead_score="hot",
        context={"request_id": "r-smoke", "conversation_summary": "Test summary."},
    )
    elapsed = time.perf_counter() - start
    non_blocking = elapsed < 0.5 and gate.wait(timeout=2)
    release.set()
    return _check("async_crm_non_blocking", non_blocking, f"{elapsed:.3f}s")


def smoke_crm_payload_fields() -> bool:
    from app.integrations.hubspot import _build_payload

    payload = _build_payload(
        {
            "name": "Jane",
            "email": "jane@acme.com",
            "project_need": "AI agent",
            "project_type": "ai_agent",
            "role": "cto",
            "industry": "healthcare",
            "decision_maker": True,
        },
        intent="sales",
        session_id="s1",
        lead_score="hot",
        context={
            "conversation_summary": "CTO exploring AI agent.",
            "lead_score_numeric": 80,
            "meeting_readiness": "ready",
            "request_id": "r1",
        },
    )
    required = [
        "conversation_summary",
        "project_type",
        "role",
        "industry",
        "lead_score_numeric",
        "meeting_readiness",
    ]
    ok = all(payload.get(k) for k in required) and "conversation_history" not in payload
    return _check("crm_payload_fields", ok, json.dumps({k: payload.get(k) for k in required}))


def smoke_live_health(base_url: str) -> bool:
    try:
        import httpx

        url = base_url.rstrip("/") + "/api/v1/health"
        resp = httpx.get(url, timeout=10.0)
        ok = resp.status_code == 200 and resp.json().get("status") in {"ok", "degraded"}
        return _check("live_health", ok, url)
    except Exception as exc:
        return _check("live_health", False, str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="P0 staging smoke checks")
    parser.add_argument("--live", action="store_true", help="Also hit STAGING_API_URL health")
    args = parser.parse_args()

    print("P0 staging smoke checks\n")
    results = [
        smoke_redis_session(),
        smoke_redis_rate_limit(),
        smoke_async_crm_non_blocking(),
        smoke_crm_payload_fields(),
    ]

    if args.live:
        base = os.environ.get("STAGING_API_URL", "http://127.0.0.1:8001")
        results.append(smoke_live_health(base))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
