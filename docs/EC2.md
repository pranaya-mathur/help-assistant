# EC2 deployment — API on a single instance

Deploy the FastAPI backend to **one EC2 instance** with Docker Compose (Postgres + Redis + API). CI/CD builds the image in GitHub Actions, pushes to **ECR**, and rolls out over **SSH**.

**DevOps handoff:** start with **[docs/DEVOPS_DEPLOY.md](DEVOPS_DEPLOY.md)** and **`.env.devops.example`**.

The chat widget stays on a separate host (S3/CloudFront); it calls this API over HTTPS (use an ALB, nginx, or Cloudflare in front of port 80).

## Architecture

```
GitHub Actions (main) → ECR → SSH → EC2
                                    ├── Docker: Postgres 16
                                    ├── Docker: Redis 7
                                    └── Docker: API (port 80 → 8080)
```

| Component | Where |
|-----------|--------|
| API container | ECR image on EC2 |
| Postgres / Redis | docker-compose.ec2.yml on same host |
| Vector index | Docker volume `app_data` |
| CI/CD | `.github/workflows/deploy-ec2.yml` |

## 1. EC2 instance

**Recommended:** Amazon Linux 2023 or Ubuntu 22.04, `t3.small` or larger (ingest needs RAM).

**Security group:**

| Port | Source | Purpose |
|------|--------|---------|
| 22 | Your IP | SSH |
| 80 | 0.0.0.0/0 or ALB | HTTP API |
| 443 | 0.0.0.0/0 | HTTPS (if terminating TLS on host) |

**IAM instance profile** (required for ECR pull on the server):

- `AmazonEC2ContainerRegistryReadOnly`

Optional: `AmazonSSMManagedInstanceCore` if you prefer Session Manager over SSH.

## 2. One-time server setup

SSH into the instance:

```bash
# Bootstrap Docker (Amazon Linux example)
sudo dnf install -y docker git awscli python3
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user

# Or run from repo:
sudo ./scripts/ec2/setup_ec2.sh
```

Clone the repo and configure env:

```bash
sudo mkdir -p /opt/mobcoder
sudo chown ec2-user:ec2-user /opt/mobcoder
# GitLab (source): https://gitlab.com/mobcoder-sales-agent/mobcoder.ai-agent
git clone https://gitlab.com/mobcoder-sales-agent/mobcoder.ai-agent.git /opt/mobcoder
cd /opt/mobcoder
cp .env.devops.example .env.production
# Edit .env.production — at minimum OPENAI_API_KEY and APIFY_API_TOKEN
```

Log out and back in so `docker` group membership applies.

## 3. ECR repository

Create once (or let CI create it):

```bash
export AWS_REGION=us-east-1
./scripts/aws/bootstrap_aws.sh
```

## 4. GitHub Actions secrets

Add these in **Settings → Secrets and variables → Actions**:

| Secret | Example | Required |
|--------|---------|----------|
| `AWS_REGION` | `us-east-1` | Yes |
| `EC2_HOST` | `ec2-xx-xx-xx-xx.compute.amazonaws.com` or Elastic IP | Yes |
| `EC2_USER` | `ec2-user` (AL) or `ubuntu` | Yes |
| `EC2_SSH_PRIVATE_KEY` | Full PEM private key for the instance | Yes |
| `AWS_ACCESS_KEY_ID` | IAM user for CI ECR push | Yes* |
| `AWS_SECRET_ACCESS_KEY` | Paired secret | Yes* |
| `EC2_APP_DIR` | `/opt/mobcoder` | No (default `/opt/mobcoder`) |
| `EC2_API_URL` | `http://YOUR_IP` or `https://api.yourdomain.com` | No (smoke test) |
| `AWS_DEPLOY_ROLE_ARN` | OIDC role (alternative to access keys) | No |

\* Use either access keys **or** `AWS_DEPLOY_ROLE_ARN` with GitHub OIDC.

CI IAM user/role needs: `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`, `ecr:PutImage`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:CreateRepository`.

## 5. Deploy

**Automatic:** push to `main` (paths in workflow) runs `.github/workflows/deploy-ec2.yml`.

**Manual:** Actions → **Deploy API to EC2** → Run workflow.

**On server (manual):**

```bash
export AWS_REGION=us-east-1
export ECR_IMAGE=123456789012.dkr.ecr.us-east-1.amazonaws.com/mobcoder-sales-agent:latest
cd /opt/mobcoder
./scripts/ec2/deploy.sh
```

## 6. Verify

```bash
curl http://YOUR_EC2_IP/api/v1/health
# vector_store_count should be > 0 after first boot (AUTO_INGEST_ON_START)
```

Or from your laptop:

```bash
API_URL=http://YOUR_EC2_IP ./scripts/aws/smoke_health.sh
```

## 7. Widget

Widget is deployed separately (GitLab CI on `dev` or `scripts/aws/deploy_widget_dev.sh`). See **[docs/DEV_WIDGET_DEPLOY.md](DEV_WIDGET_DEPLOY.md)**.

`CORS_ALLOWED_ORIGINS` in `.env.production` must include `https://devweb-agent.mobcoder.ai` (default in `.env.devops.example`).

## 8. HTTPS

Options:

- **Application Load Balancer** → target group → EC2:80 (set `TRUST_PROXY_HEADERS=true`)
- **nginx + Let's Encrypt** on the host
- **Cloudflare** proxy in front of the instance

## 9. Go-live checklist

- [ ] `.env.production` on EC2 with real `OPENAI_API_KEY`
- [ ] IAM instance profile for ECR pull
- [ ] GitHub secrets configured
- [ ] `GET /api/v1/health` → `vector_store_count > 0`
- [ ] Run `./scripts/ec2/reingest.sh` for full crawl (or rely on first-boot seed ingest)
- [ ] Set `AUTO_INGEST_ON_START=false` in `.env.production` after first successful ingest
- [ ] HTTPS + CORS for mobcoder.ai and dev widget (`devweb-agent.mobcoder.ai`)
- [ ] Security group restricts SSH to your IP

## Estimated monthly cost (us-east-1)

| Resource | Approx. |
|----------|---------|
| EC2 t3.small | ~$15 |
| EBS 30 GB gp3 | ~$2 |
| ECR storage | ~$1 |

Cheaper than full ECS + NAT + RDS for a single-tenant staging or light production workload.

## ECS vs EC2

| | EC2 (this guide) | ECS Fargate ([docs/AWS.md](AWS.md)) |
|--|------------------|-------------------------------------|
| Ops | You manage the VM | AWS manages tasks |
| Postgres/Redis | On same host (Compose) | Managed RDS + ElastiCache |
| Scale | Vertical / second instance | Horizontal via ECS |
| Cost | Lower for one box | Higher, more resilient |

Use **deploy-ec2.yml** for EC2. Use **deploy-aws.yml** if you provision the Terraform ECS stack.
