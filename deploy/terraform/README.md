# Terraform — AWS API stack

Provisions the full **API-only** production stack:

- VPC (public + private subnets, NAT)
- RDS PostgreSQL 16
- ElastiCache Redis 7
- EFS (uid/gid 1000 for Chroma)
- ECR repository
- ECS Fargate cluster + service + task definition
- Application Load Balancer
- Secrets Manager (DATABASE_URL, REDIS_URL, API keys)
- Optional GitHub Actions OIDC deploy role

## Prerequisites

- [Terraform](https://www.terraform.io/downloads) >= 1.5
- AWS CLI configured (`aws configure`)
- Docker (for first image push after apply)

## Quick start

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set openai_api_key at minimum

terraform init
terraform plan
terraform apply
```

After apply, read outputs:

```bash
terraform output deploy_commands
terraform output api_health_url
```

Push the first image and roll out:

```bash
# From repo root — use values from terraform output
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPOSITORY=mobcoder-sales-agent
export ECS_CLUSTER=$(terraform -chdir=deploy/terraform output -raw ecs_cluster_name)
export ECS_SERVICE=$(terraform -chdir=deploy/terraform output -raw ecs_service_name)

./scripts/aws/deploy_api.sh
# Or set DEPLOY_MODE=force for image-only rollout:
DEPLOY_MODE=force ./scripts/aws/deploy_api.sh
```

Smoke test:

```bash
API_URL=http://$(terraform -chdir=deploy/terraform output -raw alb_dns_name) ./scripts/aws/smoke_health.sh
```

## GitHub Actions

After `terraform apply` with `enable_github_oidc = true`, set repository secrets:

| Secret | Source |
|--------|--------|
| `AWS_REGION` | terraform output `aws_region` |
| `AWS_ACCOUNT_ID` | terraform output `aws_account_id` |
| `AWS_DEPLOY_ROLE_ARN` | terraform output `github_deploy_role_arn` |

The workflow pushes `:latest` to ECR and forces a new ECS deployment.

## Post-deploy

1. Confirm `GET /api/v1/health` → `vector_store_count > 0`
2. Set `auto_ingest_on_start = false` in `terraform.tfvars` and run `terraform apply`
3. Add ACM certificate + `acm_certificate_arn` for HTTPS
4. Point widget at `https://your-alb-or-domain/api/v1/chat`

## Destroy

```bash
terraform destroy
```

⚠️ Production RDS has `deletion_protection = true` when `environment = "prod"`.

## Staging / dev on AWS

Set `environment = "staging"` or `"dev"` in `terraform.tfvars` (not `prod`).

| Setting | `prod` | `staging` / `dev` |
|---------|--------|-------------------|
| `/docs`, `/sources`, debug endpoints | Off | **On** |
| RDS deletion protection | On | Off |
| ECR `force_delete` | Off | On |
| HTTPS (ACM) | Recommended | Optional (HTTP ALB OK for testing) |

Recommended dev sizing in `terraform.tfvars`:

```hcl
environment       = "staging"
ecs_desired_count = 1
enable_github_oidc = false   # manual deploy via scripts/aws/deploy_api.sh
```

After deploy, open Swagger at `http://<alb-dns>/docs` (from `terraform output alb_dns_name`).

Destroy when done testing: `terraform destroy`
