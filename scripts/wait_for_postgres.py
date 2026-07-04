#!/usr/bin/env python3
"""Block until Postgres accepts connections (Docker / k8s startup)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.infra.postgres import verify_postgres_connectivity


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("[wait_for_postgres] DATABASE_URL unset — skipping")
        return 0

    timeout = int(os.environ.get("POSTGRES_WAIT_SECONDS", "60"))
    deadline = time.monotonic() + timeout
    last_error = ""

    while time.monotonic() < deadline:
        try:
            verify_postgres_connectivity(url)
            print("[wait_for_postgres] Postgres is ready")
            return 0
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2)

    print(f"[wait_for_postgres] timed out after {timeout}s: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
