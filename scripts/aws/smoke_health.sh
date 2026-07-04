#!/usr/bin/env bash
# Smoke test a live API deployment.
# Usage: API_URL=https://api.example.com ./scripts/aws/smoke_health.sh

set -euo pipefail

: "${API_URL:?Set API_URL e.g. https://api.example.com}"

echo "[smoke] GET ${API_URL}/api/v1/health"
BODY="$(curl -fsS "${API_URL}/api/v1/health")"
echo "${BODY}" | python3 -m json.tool

CHUNKS="$(echo "${BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('vector_store_count',0))")"
if [ "${CHUNKS}" -le 0 ] 2>/dev/null; then
  echo "[smoke] WARN: vector_store_count is 0 — run ingest or enable AUTO_INGEST_ON_START once"
fi

echo "[smoke] OK"
