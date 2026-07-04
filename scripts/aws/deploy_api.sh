#!/usr/bin/env bash
# Build, push to ECR, and roll out ECS.
#
# Modes:
#   DEPLOY_MODE=force  — push image :latest + force-new-deployment (Terraform-managed task def)
#   DEPLOY_MODE=full   — also register task definition from deploy/ecs/task-definition.json
#
# Usage:
#   export AWS_REGION=us-east-1
#   export AWS_ACCOUNT_ID=123456789012
#   export ECR_REPOSITORY=mobcoder-sales-agent
#   export ECS_CLUSTER=mobcoder-sales-agent-prod-cluster
#   export ECS_SERVICE=mobcoder-sales-agent-prod-api
#   DEPLOY_MODE=force ./scripts/aws/deploy_api.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

: "${AWS_REGION:?Set AWS_REGION}"
: "${AWS_ACCOUNT_ID:?Set AWS_ACCOUNT_ID}"
: "${ECR_REPOSITORY:?Set ECR_REPOSITORY}"
: "${ECS_CLUSTER:?Set ECS_CLUSTER}"
: "${ECS_SERVICE:?Set ECS_SERVICE}"

DEPLOY_MODE="${DEPLOY_MODE:-force}"

IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY}"
IMAGE="${ECR_URI}:${IMAGE_TAG}"

echo "[deploy] Ensuring ECR repository exists..."
aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null

echo "[deploy] Logging in to ECR..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "[deploy] Building image..."
docker build -t "${ECR_REPOSITORY}:${IMAGE_TAG}" -t "${ECR_URI}:latest" .

echo "[deploy] Pushing ${ECR_URI}:latest ..."
docker push "${ECR_URI}:${IMAGE_TAG}"
docker push "${ECR_URI}:latest"

if [ "${DEPLOY_MODE}" = "full" ]; then
  TASK_DEF_SRC="${ROOT}/deploy/ecs/task-definition.json"
  TASK_DEF_RENDERED="$(mktemp)"
  sed \
    -e "s/ACCOUNT_ID/${AWS_ACCOUNT_ID}/g" \
    -e "s/AWS_REGION/${AWS_REGION}/g" \
    -e "s|mobcoder-sales-agent:latest|${ECR_REPOSITORY}:${IMAGE_TAG}|g" \
    "${TASK_DEF_SRC}" > "${TASK_DEF_RENDERED}"
  echo "[deploy] Registering task definition..."
  TASK_ARN="$(aws ecs register-task-definition \
    --cli-input-json "file://${TASK_DEF_RENDERED}" \
    --region "${AWS_REGION}" \
    --query 'taskDefinition.taskDefinitionArn' \
    --output text)"
  rm -f "${TASK_DEF_RENDERED}"
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --task-definition "${TASK_ARN}" \
    --force-new-deployment \
    --region "${AWS_REGION}" >/dev/null
else
  echo "[deploy] Force new deployment (Terraform-managed task definition, image :latest)..."
  aws ecs update-service \
    --cluster "${ECS_CLUSTER}" \
    --service "${ECS_SERVICE}" \
    --force-new-deployment \
    --region "${AWS_REGION}" >/dev/null
fi

echo "[deploy] Waiting for service stability..."
aws ecs wait services-stable \
  --cluster "${ECS_CLUSTER}" \
  --services "${ECS_SERVICE}" \
  --region "${AWS_REGION}"

echo "[deploy] Done."
echo "[deploy] Smoke: API_URL=http://YOUR-ALB-DNS ./scripts/aws/smoke_health.sh"
