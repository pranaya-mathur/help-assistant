#!/usr/bin/env bash
# Pull ECR image and roll out docker-compose.ec2.yml on the EC2 host.
#
# Run on EC2 (or invoked via SSH from GitHub Actions):
#   export ECR_IMAGE=123456789012.dkr.ecr.us-east-1.amazonaws.com/mobcoder-sales-agent:abc1234
#   export AWS_REGION=us-east-1
#   ./scripts/ec2/deploy.sh
#
# Optional:
#   APP_DIR=/opt/mobcoder
#   COMPOSE_FILE=docker-compose.ec2.yml
#   ENV_FILE=.env.production

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP_DIR="${APP_DIR:-$ROOT}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.ec2.yml}"
ENV_FILE="${ENV_FILE:-.env.production}"

: "${ECR_IMAGE:?Set ECR_IMAGE}"
: "${AWS_REGION:?Set AWS_REGION}"

cd "$APP_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "[deploy] ERROR: $APP_DIR/$ENV_FILE not found. Copy .env.ec2.example and fill secrets."
  exit 1
fi

ECR_REGISTRY="${ECR_IMAGE%%/*}"
echo "[deploy] Logging in to ECR (${ECR_REGISTRY})..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${ECR_REGISTRY}"

echo "[deploy] Pulling ${ECR_IMAGE}..."
export ECR_IMAGE
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" pull mobcoder-api

echo "[deploy] Starting stack..."
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d --remove-orphans

echo "[deploy] Waiting for API health..."
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${API_HOST_PORT:-80}/api/v1/health" >/dev/null 2>&1; then
    echo "[deploy] API healthy."
    curl -fsS "http://127.0.0.1:${API_HOST_PORT:-80}/api/v1/health" | python3 -m json.tool 2>/dev/null || true
    echo "[deploy] Done."
    exit 0
  fi
  sleep 5
done

echo "[deploy] ERROR: API did not become healthy within 5 minutes."
docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" logs --tail=80 mobcoder-api || true
exit 1
