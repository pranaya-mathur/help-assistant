#!/bin/sh
set -e

API_PORT="${API_PORT:-8080}"
UVICORN_WORKERS="${UVICORN_WORKERS:-1}"

echo "[entrypoint] MobCoder API starting (APP_ENV=${APP_ENV:-development})"

# Wait for Redis when production backends are enabled
if [ -n "${REDIS_URL:-}" ]; then
  python scripts/wait_for_redis.py
fi

# Wait for Postgres when DATABASE_URL is set
if [ -n "${DATABASE_URL:-}" ]; then
  python scripts/wait_for_postgres.py
fi

# Apply schema migrations before serving traffic
if [ "${AUTO_MIGRATE_DB:-true}" = "true" ] && [ -n "${DATABASE_URL:-}" ]; then
  python scripts/migrate_db.py
fi

# Seed + ingest on first boot when Chroma is genuinely empty (needs OPENAI_API_KEY).
# Exit codes distinguish "empty" from "errored" so a transient Chroma lock/corruption
# on boot (e.g. concurrent replica restart on EFS) never triggers a destructive
# --reset-collection — that would silently wipe a healthy index over a boot-time blip.
if [ "${AUTO_INGEST_ON_START:-true}" = "true" ]; then
  # `if <cmd>` is exempt from `set -e` aborting the script on non-zero exit —
  # deliberately used here since the python check exits 1 (empty) or 2 (error)
  # by design, and a bare `cmd; rc=$?` under `set -e` would kill the entrypoint
  # right after the check, before uvicorn ever starts.
  if python -c "
from app.rag.vector_store import get_vector_store
try:
    n = get_vector_store().count()
except Exception as exc:
    print(f'[entrypoint-check] vector store count() failed: {exc}')
    raise SystemExit(2)
raise SystemExit(0 if n > 0 else 1)
"; then
    vs_check_rc=0
  else
    vs_check_rc=$?
  fi

  if [ "$vs_check_rc" -eq 0 ]; then
    echo "[entrypoint] vector store already populated"
  elif [ "$vs_check_rc" -eq 1 ]; then
    if [ -n "${OPENAI_API_KEY:-}" ]; then
      echo "[entrypoint] empty vector store — running seed + ingest"
      python scripts/seed_knowledge.py --ingest
    else
      echo "[entrypoint] WARN: vector store empty and OPENAI_API_KEY unset; RAG will use seed fallback only"
    fi
  else
    echo "[entrypoint] WARN: could not determine vector store state (see error above)."
    echo "[entrypoint] WARN: NOT running seed/ingest — refusing to reset a possibly-healthy index on a transient error."
    echo "[entrypoint] WARN: if the store really is broken, fix/investigate manually then re-run scripts/ingest.py --reset-collection."
  fi
fi

mkdir -p data/sessions data/embeddings/chroma data/chunks data/indexes
mkdir -p /mnt/efs/chroma /mnt/efs/indexes 2>/dev/null || true

echo "[entrypoint] uvicorn on 0.0.0.0:${API_PORT} workers=${UVICORN_WORKERS}"
exec uvicorn main:app \
  --host 0.0.0.0 \
  --port "${API_PORT}" \
  --workers "${UVICORN_WORKERS}" \
  --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
