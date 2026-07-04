#!/usr/bin/env python3
"""Delete chat sessions older than the configured TTL (default 30 days)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings
from app.infra.postgres import prune_expired_sessions, verify_postgres_connectivity


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune expired chat sessions from Postgres")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Delete sessions with updated_at older than this many days (default: 30)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    verify_postgres_connectivity(settings.database_url)
    deleted = prune_expired_sessions(args.days)
    print(f"Pruned {deleted} expired session(s) (TTL {args.days} days)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
