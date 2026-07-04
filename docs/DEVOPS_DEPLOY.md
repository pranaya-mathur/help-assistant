# DevOps deployment guide

Single handoff doc for deploying the MobCoder Sales & Help Assistant API and widget.

| Role | URL (dev pilot) |
|------|-----------------|
| API | https://devapi-chatbot.mobcoder.ai |
| Widget | https://devweb-agent.mobcoder.ai |

---

## Quick reference

| Task | Command / artifact |
|------|-------------------|
| **Start here** | This file |
| **EC2 env template** | `.env.devops.example` → copy to `.env.production` on server |
| **EC2 stack** | `docker-compose.ec2.yml` |
| **One-time EC2 bootstrap** | `sudo ./scripts/ec2/setup_ec2.sh` |
| **Deploy API (CI)** | Push to `main` → `.github/workflows/deploy-ec2.yml` |
| **Deploy API (manual on EC2)** | `./scripts/ec2/deploy.sh` |
| **Refresh knowledge on EC2** | `./scripts/ec2/reingest.sh` |
| **Deploy widget (CI)** | Push to `dev` → GitLab `.gitlab-ci.yml` |
| **Deploy widget (manual)** | `./scripts/aws/deploy_widget_dev.sh` |
| **Smoke test** | `API_URL=https://devapi-chatbot.mobcoder.ai ./scripts/aws/smoke_health.sh` |
| **ECS / Terraform** | `docs/AWS.md`, `deploy/terraform/` |

---

## Architecture

```text
Widget (S3/CloudFront, GitLab CI)     API (EC2, GitHub Actions)
devweb-agent.mobcoder.ai    ──HTTPS──▶  devapi-chatbot.mobcoder.ai
                                              ├── Postgres 16
                                              ├── Redis 7
                                              └── FastAPI (port 8080)
```

- **API CI/CD:** GitHub Actions on `main` — builds Docker image, pushes ECR, SSH deploy to EC2.
- **Widget CI/CD:** GitLab CI on `dev` branch — uploads `widget/demo.html` + `mobcoder-chat.js` to S3.
- **Knowledge:** Stored in Docker volume `app_data` on EC2 (Chroma + BM25). CI weekly crawl does **not** update the live server — run `reingest.sh` on EC2 after code deploy or on a schedule.

---

## 1. Prerequisites

### AWS (API on EC2)

| Item | Notes |
|------|-------|
| EC2 instance | Amazon Linux 2023 or Ubuntu 22.04, `t3.small`+ |
| IAM instance profile | `AmazonEC2ContainerRegistryReadOnly` (ECR pull) |
| ECR repository | `mobcoder-sales-agent` (CI can create) |
| Security group | 22 (SSH, your IP), 80/443 (API) |

### GitHub Actions secrets (API deploy)

| Secret | Required | Example |
|--------|----------|---------|
| `AWS_REGION` | Yes | `us-east-1` |
| `EC2_HOST` | Yes | EC2 hostname or Elastic IP |
| `EC2_USER` | Yes | `ec2-user` or `ubuntu` |
| `EC2_SSH_PRIVATE_KEY` | Yes | PEM private key |
| `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Yes* | ECR push from CI |
| `AWS_DEPLOY_ROLE_ARN` | Alt* | OIDC role instead of access keys |
| `EC2_APP_DIR` | No | Default `/opt/mobcoder` |
| `EC2_API_URL` | No | Enables post-deploy smoke test |

### GitLab CI variables (widget deploy)

| Variable | Required |
|----------|----------|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | Yes |
| `WIDGET_S3_BUCKET` | Yes |
| `CLOUDFRONT_DISTRIBUTION_ID` | Recommended |

---

## 2. EC2 one-time setup

SSH into the instance:

```bash
sudo ./scripts/ec2/setup_ec2.sh

sudo mkdir -p /opt/mobcoder
sudo chown ec2-user:ec2-user /opt/mobcoder
```

Clone or sync repo files to `/opt/mobcoder` (CI syncs compose + deploy scripts; you need `.env.production` on the server):

```bash
cd /opt/mobcoder
cp .env.devops.example .env.production
# Edit .env.production — set OPENAI_API_KEY and APIFY_API_TOKEN (minimum)
```

**Do not commit `.env.production` with real secrets.**

---

## 3. Deploy API

### Automatic (recommended)

Push to `main`. Workflow `.github/workflows/deploy-ec2.yml` will:

1. Build and push image to ECR (`mobcoder-sales-agent:<sha>` and `:latest`)
2. SCP `docker-compose.ec2.yml` and `scripts/ec2/deploy.sh` to EC2
3. Run deploy on the host
4. Smoke test if `EC2_API_URL` secret is set

### Manual on EC2

```bash
export AWS_REGION=us-east-1
export ECR_IMAGE=123456789012.dkr.ecr.us-east-1.amazonaws.com/mobcoder-sales-agent:latest
cd /opt/mobcoder
./scripts/ec2/deploy.sh
```

---

## 4. Deploy widget

Widget and API are on **separate hosts**. After API deploy, update the widget if `widget/` changed:

**GitLab (automatic):** push to `dev` with changes under `widget/`.

**Manual:**

```bash
export WIDGET_S3_BUCKET=<bucket-for-devweb-agent>
export AWS_REGION=us-east-1
export CLOUDFRONT_DISTRIBUTION_ID=<optional>
./scripts/aws/deploy_widget_dev.sh
```

Verify `widget/demo.html` references `devapi-chatbot.mobcoder.ai`.

---

## 5. Verify deployment

```bash
# API health
API_URL=https://devapi-chatbot.mobcoder.ai ./scripts/aws/smoke_health.sh

# Expect vector_store_count > 0 after first ingest
curl -s https://devapi-chatbot.mobcoder.ai/api/v1/health | python3 -m json.tool

# Widget points at API
curl -s https://devweb-agent.mobcoder.ai/ | grep devapi-chatbot
```

Open the widget, send a chat message — Network tab should show `POST` to `/api/v1/chat/stream`.

### CORS

`.env.production` on EC2 **must** include the widget origin:

```bash
CORS_ALLOWED_ORIGINS=https://devweb-agent.mobcoder.ai,https://mobcoder.ai,https://www.mobcoder.ai
```

This is the default in `.env.devops.example`. Missing CORS causes browser blocked requests.

---

## 6. Refresh knowledge (re-ingest)

First boot runs seed ingest when Chroma is empty (`AUTO_INGEST_ON_START=true`). For a **full mobcoder.ai crawl** on the server:

```bash
cd /opt/mobcoder
chmod +x scripts/ec2/reingest.sh
./scripts/ec2/reingest.sh
```

Options:

| Env var | Effect |
|---------|--------|
| `MAX_PAGES=500` | Crawl page limit (default 500) |
| `SKIP_CRAWL=1` | Re-ingest only from existing `pages_latest.json` |
| `RESET_COLLECTION=1` | Clean Chroma re-ingest |

After successful ingest, set `AUTO_INGEST_ON_START=false` in `.env.production` and redeploy to avoid re-seeding on every restart.

### Optional: Google Sheets + Chat (lead visibility)

While HubSpot CRM is pending, add these to `.env.production` on EC2 (not in Terraform yet):

| Variable | Guide |
|----------|--------|
| `GOOGLE_SHEETS_WEBHOOK_URL` | **[docs/GOOGLE_SHEETS_LEAD_LOG.md](GOOGLE_SHEETS_LEAD_LOG.md)** — upsert consenting leads to a sheet |
| `GOOGLE_CHAT_WEBHOOK_URL` | **[docs/GOOGLE_CHAT_LEAD_ALERTS.md](GOOGLE_CHAT_LEAD_ALERTS.md)** — one-time hot-lead alert per session |

Both require visitor **email** + **`lead_consent`**. Unset → silently disabled.

---

## 7. Go-live checklist

- [ ] `.env.production` on EC2 with real `OPENAI_API_KEY` (and `APIFY_API_TOKEN` for crawl)
- [ ] `CORS_ALLOWED_ORIGINS` includes widget origin (`devweb-agent.mobcoder.ai`)
- [ ] IAM instance profile for ECR pull on EC2
- [ ] GitHub secrets configured; `deploy-ec2` workflow green
- [ ] `GET /api/v1/health` → `vector_store_count > 0`, recent `last_ingest_at`
- [ ] Widget deployed (`deploy_widget_dev` or GitLab pipeline)
- [ ] Chat works end-to-end from widget URL
- [ ] `AUTO_INGEST_ON_START=false` after first successful full ingest
- [ ] HTTPS in front of API (ALB, nginx, or Cloudflare); `TRUST_PROXY_HEADERS=true`
- [ ] SSH restricted to your IP

---

## 8. ECS Fargate (alternative)

For multi-AZ managed infra (RDS + ElastiCache + EFS + ECS):

1. `cd deploy/terraform && cp terraform.tfvars.example terraform.tfvars`
2. `terraform init && terraform apply`
3. Configure GitHub secrets from Terraform outputs (`ECS_CLUSTER`, `ECS_SERVICE`, `AWS_DEPLOY_ROLE_ARN`)
4. Push to `main` → `.github/workflows/deploy-aws.yml`

Details: **[docs/AWS.md](AWS.md)**, env reference: **`.env.aws.example`**.

---

## 9. Troubleshooting

| Symptom | Fix |
|---------|-----|
| CORS error in browser | Add widget origin to `CORS_ALLOWED_ORIGINS` in `.env.production`, restart API |
| `vector_store_count: 0` | Run `./scripts/ec2/reingest.sh` or set `AUTO_INGEST_ON_START=true` once |
| Stale answers / old behavior | Redeploy API **and** widget; run `reingest.sh` |
| Deploy workflow didn't run | Check path filters — changes under `prompts/`, `scripts/`, `data/formatted/pages_seed.json` now trigger deploy |
| API unhealthy after deploy | `docker compose -f docker-compose.ec2.yml --env-file .env.production logs mobcoder-api` |

---

## Related docs

- [docs/EC2.md](EC2.md) — EC2 instance sizing, HTTPS, security groups
- [docs/AWS.md](AWS.md) — Terraform ECS stack
- [docs/DEV_WIDGET_DEPLOY.md](DEV_WIDGET_DEPLOY.md) — widget ↔ API linking
- [docs/PRODUCTION.md](PRODUCTION.md) — production flags, hybrid retrieval, OTel
