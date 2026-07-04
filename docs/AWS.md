# AWS deployment — API only

Deploy the FastAPI backend to **Amazon ECS Fargate**. The chat widget stays on your website (mobcoder.ai); it calls this API over HTTPS.

## Recommended: Terraform (one command infrastructure)

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set openai_api_key

terraform init
terraform apply
```

This creates: VPC, RDS PostgreSQL, ElastiCache Redis, EFS, ECR, ECS, ALB, Secrets Manager, IAM, optional GitHub OIDC role.

Full details: **[deploy/terraform/README.md](../deploy/terraform/README.md)**

After apply:

```bash
terraform output deploy_commands
# Push image + deploy from repo root:
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPOSITORY=mobcoder-sales-agent
export ECS_CLUSTER=$(terraform -chdir=deploy/terraform output -raw ecs_cluster_name)
export ECS_SERVICE=$(terraform -chdir=deploy/terraform output -raw ecs_service_name)
DEPLOY_MODE=force ./scripts/aws/deploy_api.sh
```

GitHub Actions secrets (after `enable_github_oidc = true`):

| Secret | Terraform output |
|--------|------------------|
| `AWS_REGION` | `aws_region` |
| `AWS_DEPLOY_ROLE_ARN` | `github_deploy_role_arn` |
| `ECS_CLUSTER` | `ecs_cluster_name` |
| `ECS_SERVICE` | `ecs_service_name` |
| `API_URL` | ALB DNS (optional — enables post-deploy smoke test in `deploy-aws.yml`) |

---

## Architecture

```
Internet → ALB (HTTPS) → ECS Fargate (Dockerfile)
                              ├── Amazon RDS (PostgreSQL) — sessions, feedback, analytics
                              ├── ElastiCache (Redis) — rate limiting
                              └── EFS — Chroma vector index + BM25 index
```

| Component | AWS service |
|-----------|-------------|
| API container | ECS Fargate |
| Load balancer | Application Load Balancer |
| Postgres | RDS PostgreSQL 16 |
| Redis | ElastiCache Redis 7 |
| Vector store | EFS mounted at `/mnt/efs` |
| Secrets | Secrets Manager |
| Logs | CloudWatch Logs |
| CI/CD | GitHub Actions → ECR → ECS |

Repo artifacts:

| Path | Purpose |
|------|---------|
| `deploy/terraform/` | **Primary** — full AWS stack |
| `Dockerfile` | Container image |
| `.env.aws.example` | Non-secret env reference |
| `deploy/ecs/task-definition.json` | Manual/legacy task def (Terraform preferred) |
| `scripts/aws/deploy_api.sh` | Build/push/deploy |
| `.github/workflows/deploy-aws.yml` | CI deploy on `main` |

---

## Manual setup (without Terraform)

Use this only if you cannot use Terraform. See sections below for RDS, ElastiCache, EFS, ECS, ALB.

```bash
export AWS_REGION=us-east-1
./scripts/aws/bootstrap_aws.sh
```

Then follow `deploy/ecs/task-definition.json` and fill all placeholders manually.

---

## Widget (local demo)

Serve [`widget/demo.html`](../widget/demo.html) (e.g. `cd widget && python -m http.server 8765`). The page sets `LOCAL_API_BASE` / `API_BASE` and derives `API_URL` and `STREAM_URL` for your API HTTPS base.

Set `cors_allowed_origins` in Terraform to include your site origins. Add `acm_certificate_arn` for HTTPS on the ALB.

---

## Go-live checklist

- [ ] `terraform apply` succeeded
- [ ] `openai_api_key` set in Secrets Manager (not `REPLACE_ME`)
- [ ] Docker image pushed to ECR (`deploy_api.sh`)
- [ ] `GET /api/v1/health` → `vector_store_count > 0`
- [ ] Set `auto_ingest_on_start = false` and re-apply Terraform
- [ ] HTTPS via ACM certificate (production)
- [ ] Widget points at API URL; CORS allows mobcoder.ai
- [ ] `API_URL=... ./scripts/aws/smoke_health.sh` passes

---

## Estimated monthly cost (us-east-1, defaults)

| Resource | Approx. |
|----------|---------|
| NAT Gateway | ~$32 + data |
| RDS db.t4g.micro | ~$12 |
| ElastiCache cache.t4g.micro | ~$12 |
| ECS Fargate (2 × 1 vCPU) | ~$30–60 |
| ALB | ~$16 |
| EFS | ~$1–5 |

Use `ecs_desired_count = 1` and smaller instances for staging.

