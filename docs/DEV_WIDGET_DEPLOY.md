# Link dev widget ↔ dev API (AWS)

**Full DevOps guide:** [docs/DEVOPS_DEPLOY.md](DEVOPS_DEPLOY.md)

| Role | URL |
|------|-----|
| Frontend (widget) | https://devweb-agent.mobcoder.ai |
| Backend (API) | https://devapi-chatbot.mobcoder.ai |

API is already live. The widget must call `https://devapi-chatbot.mobcoder.ai` (configured in `widget/demo.html`).

## What blocks chat today

**Update (4 July 2026):** the original blocker described below (S3 serving an old `index.html` pointed at `127.0.0.1:8001`) is resolved — verified live: `devweb-agent.mobcoder.ai` correctly points at `devapi-chatbot.mobcoder.ai`, and the API responds healthy.

**Two new gaps found instead, verified live today:**
1. **The deployed widget predates every bug fix made in this codebase on 4 July 2026** (popup-dismiss suppression, escalation-modal-reopening guard, qualify-nag cooldown, and others). None of it has been redeployed here.
2. **The deployed knowledge base is stale** — `GET /api/v1/health` on `devapi-chatbot.mobcoder.ai` reports `vector_store_count: 17`, `last_ingest_at: 2026-06-18` — 17 chunks from over two weeks ago, vs. the 257-chunk index re-crawled and re-ingested locally today. Anyone testing chat quality against this dev URL right now is seeing pre-fix behavior and a much smaller, older knowledge base than what's been verified in this session.

Original historical note (now resolved, kept for context): S3 used to serve an **old** `index.html` with `http://127.0.0.1:8001`. Fixed by uploading `widget/demo.html` as `index.html` in the widget S3 bucket and invalidating CloudFront.

## Option A — GitLab CI (recommended)

1. GitLab → **Settings → CI/CD → Variables**:
   - `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`
   - `WIDGET_S3_BUCKET` — bucket name for `devweb-agent.mobcoder.ai`
   - `CLOUDFRONT_DISTRIBUTION_ID` — optional but recommended
2. Push `dev` branch with updated `widget/demo.html`
3. Pipeline **deploy-widget-dev** runs automatically
4. Verify chat on https://devweb-agent.mobcoder.ai

## Option B — AWS CLI (one-time)

```bash
export WIDGET_S3_BUCKET=<bucket-name>
export AWS_REGION=us-east-1
export CLOUDFRONT_DISTRIBUTION_ID=<distribution-id>   # optional

./scripts/aws/deploy_widget_dev.sh
```

## Option C — AWS Console

1. S3 → bucket for `devweb-agent.mobcoder.ai`
2. Upload `widget/demo.html` as **`index.html`** (root)
3. CloudFront → distribution → **Create invalidation** → `/*`

## Verify link

```bash
curl -s https://devapi-chatbot.mobcoder.ai/api/v1/health
# → {"status":"ok",...}

curl -s https://devweb-agent.mobcoder.ai/ | grep devapi-chatbot
# → should match devapi-chatbot.mobcoder.ai
```

Open the widget, send a message — Network tab should show requests to `https://devapi-chatbot.mobcoder.ai/api/v1/chat/stream`.

## API CORS

Backend must allow the widget origin (already required on server `.env`):

```bash
CORS_ALLOWED_ORIGINS=...,https://devweb-agent.mobcoder.ai,...
```
