#!/usr/bin/env bash
# One-time AWS bootstrap: ECR repo, CloudWatch log group, Secrets Manager skeleton.
# Does NOT create RDS/ElastiCache/ECS/ALB — see docs/AWS.md for those steps.
#
# Usage:
#   export AWS_REGION=us-east-1
#   ./scripts/aws/bootstrap_aws.sh

set -euo pipefail

: "${AWS_REGION:?Set AWS_REGION}"
ECR_REPOSITORY="${ECR_REPOSITORY:-mobcoder-sales-agent}"
LOG_GROUP="${LOG_GROUP:-/ecs/mobcoder-sales-api}"
SECRET_NAME="${SECRET_NAME:-mobcoder/production/app}"

echo "[bootstrap] ECR repository ${ECR_REPOSITORY}..."
aws ecr describe-repositories --repository-names "${ECR_REPOSITORY}" --region "${AWS_REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository \
    --repository-name "${ECR_REPOSITORY}" \
    --image-scanning-configuration scanOnPush=true \
    --region "${AWS_REGION}"

echo "[bootstrap] CloudWatch log group ${LOG_GROUP}..."
aws logs create-log-group --log-group-name "${LOG_GROUP}" --region "${AWS_REGION}" 2>/dev/null || true

echo "[bootstrap] Secrets Manager secret ${SECRET_NAME} (if missing)..."
if ! aws secretsmanager describe-secret --secret-id "${SECRET_NAME}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  aws secretsmanager create-secret \
    --name "${SECRET_NAME}" \
    --description "MobCoder sales agent API production secrets" \
    --secret-string '{"OPENAI_API_KEY":"","DATABASE_URL":"","REDIS_URL":"","APIFY_API_TOKEN":""}' \
    --region "${AWS_REGION}"
  echo "  Created empty secret — update values in AWS Console or CLI."
else
  echo "  Secret already exists."
fi

echo "[bootstrap] Done. Next: provision RDS, ElastiCache, EFS, ECS — see docs/AWS.md"
