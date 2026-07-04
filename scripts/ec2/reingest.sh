#!/usr/bin/env bash
# Re-crawl mobcoder.ai and re-ingest Chroma + BM25 on the EC2 host.
#
# Run on EC2 (inside the running API container) after deploy or on a schedule.
# Requires APIFY_API_TOKEN and OPENAI_API_KEY in .env.production.
#
# Usage (on EC2):
#   cd /opt/mobcoder
#   ./scripts/ec2/reingest.sh
#
# Optional:
#   COMPOSE_FILE=docker-compose.ec2.yml
#   ENV_FILE=.env.production
#   MAX_PAGES=500
#   SKIP_CRAWL=1          # only re-ingest from existing pages_latest.json
#   RESET_COLLECTION=1    # pass --reset-collection to ingest.py

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="${APP_DIR:-$ROOT}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ec2.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"
MAX_PAGES="${MAX_PAGES:-500}"
SERVICE="${SERVICE:-mobcoder-api}"

cd "$APP_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[reingest] ERROR: $APP_DIR/$ENV_FILE not found." >&2
  exit 1
fi

compose() {
  docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
}

echo "[reingest] Checking API container is up..."
compose ps --status running --services | grep -qx "${SERVICE}" || {
  echo "[reingest] ERROR: service ${SERVICE} is not running. Start stack first." >&2
  exit 1
}

if [ "${SKIP_CRAWL:-0}" != "1" ]; then
  echo "[reingest] Crawling mobcoder.ai (max ${MAX_PAGES} pages)..."
  compose exec -T "${SERVICE}" \
    python scripts/crawl_mobcoder.py --use-sitemap --max-pages "${MAX_PAGES}"
else
  echo "[reingest] SKIP_CRAWL=1 — using existing data/formatted/pages_latest.json"
fi

INGEST_ARGS=()
if [ "${RESET_COLLECTION:-0}" = "1" ]; then
  INGEST_ARGS+=(--reset-collection)
fi

echo "[reingest] Ingesting into Chroma (+ BM25 index)..."
compose exec -T "${SERVICE}" python scripts/ingest.py "${INGEST_ARGS[@]}"

echo "[reingest] Health check..."
curl -fsS "http://127.0.0.1:${API_HOST_PORT:-80}/api/v1/health" | python3 -m json.tool

echo "[reingest] Done. Set AUTO_INGEST_ON_START=false in .env.production if first boot ingest is complete."
