#!/usr/bin/env bash
# Upload dev widget assets to S3 + optional CloudFront invalidation.
#
# Required env:
#   WIDGET_S3_BUCKET   e.g. devweb-agent.mobcoder.ai (ask DevOps for exact bucket name)
#   AWS_REGION         e.g. us-east-1
#
# Optional:
#   CLOUDFRONT_DISTRIBUTION_ID   invalidate cache after upload
#   WIDGET_S3_KEY                default: index.html (demo.html)
#
# Uploads:
#   widget/demo.html        → s3://$BUCKET/index.html
#   widget/mobcoder-chat.js → s3://$BUCKET/mobcoder-chat.js
#
# Usage:
#   export WIDGET_S3_BUCKET=your-bucket AWS_REGION=us-east-1
#   ./scripts/aws/deploy_widget_dev.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_SRC="${ROOT}/widget/demo.html"
JS_SRC="${ROOT}/widget/mobcoder-chat.js"
KEY="${WIDGET_S3_KEY:-index.html}"

: "${WIDGET_S3_BUCKET:?Set WIDGET_S3_BUCKET}"
: "${AWS_REGION:?Set AWS_REGION}"

if [[ ! -f "${DEMO_SRC}" ]]; then
  echo "[widget] missing ${DEMO_SRC}" >&2
  exit 1
fi

if [[ ! -f "${JS_SRC}" ]]; then
  echo "[widget] missing ${JS_SRC}" >&2
  exit 1
fi

if ! grep -q 'devapi-chatbot.mobcoder.ai' "${DEMO_SRC}"; then
  echo "[widget] demo.html does not reference devapi-chatbot — fix before deploy" >&2
  exit 1
fi

echo "[widget] Uploading ${DEMO_SRC} → s3://${WIDGET_S3_BUCKET}/${KEY}"
aws s3 cp "${DEMO_SRC}" "s3://${WIDGET_S3_BUCKET}/${KEY}" \
  --region "${AWS_REGION}" \
  --content-type "text/html; charset=utf-8" \
  --cache-control "public, max-age=60"

echo "[widget] Uploading ${JS_SRC} → s3://${WIDGET_S3_BUCKET}/mobcoder-chat.js"
aws s3 cp "${JS_SRC}" "s3://${WIDGET_S3_BUCKET}/mobcoder-chat.js" \
  --region "${AWS_REGION}" \
  --content-type "application/javascript; charset=utf-8" \
  --cache-control "public, max-age=300"

if [[ -n "${CLOUDFRONT_DISTRIBUTION_ID:-}" ]]; then
  echo "[widget] CloudFront invalidation ${CLOUDFRONT_DISTRIBUTION_ID} /*"
  aws cloudfront create-invalidation \
    --distribution-id "${CLOUDFRONT_DISTRIBUTION_ID}" \
    --paths "/*" \
    --output text --query 'Invalidation.Id'
fi

echo "[widget] Done."
echo "[widget]   Demo:  https://devweb-agent.mobcoder.ai/"
echo "[widget]   Embed: https://devweb-agent.mobcoder.ai/mobcoder-chat.js"
