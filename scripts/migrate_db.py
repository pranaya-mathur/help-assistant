#!/usr/bin/env python3
"""Apply pending SQL migrations in db/migrations/."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config.settings import get_settings
from app.infra.postgres import run_migrations, verify_postgres_connectivity


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Postgres schema migrations")
    parser.add_argument(
        "--database-url",
        default="",
        help="Override DATABASE_URL from environment",
    )
    args = parser.parse_args()

    settings = get_settings()
    url = (args.database_url or settings.database_url or "").strip()
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 1

    verify_postgres_connectivity(url)
    applied = run_migrations(url)
    if applied:
        print(f"Applied migrations: {', '.join(applied)}")
    else:
        print("Database schema is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
