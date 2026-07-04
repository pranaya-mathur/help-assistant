#!/usr/bin/env python3
"""Block until Redis accepts connections (used by Docker entrypoint)."""
from __future__ import annotations

import os
import sys
import time


def main() -> int:
    url = (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return 0

    try:
        import redis
    except ImportError:
        print("wait_for_redis: redis package not installed", file=sys.stderr)
        return 1

    timeout = int(os.environ.get("REDIS_WAIT_SECONDS", "60"))
    deadline = time.time() + timeout
    last_err: Exception | None = None

    while time.time() < deadline:
        try:
            client = redis.from_url(url, socket_connect_timeout=2)
            client.ping()
            print(f"wait_for_redis: connected ({url})")
            return 0
        except Exception as exc:
            last_err = exc
            time.sleep(2)

    print(f"wait_for_redis: timed out after {timeout}s: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
