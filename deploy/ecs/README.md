# Legacy ECS task definition (manual deploy only)

**Prefer Terraform:** `deploy/terraform/` is the primary AWS stack. This JSON is for manual/bootstrap setups when Terraform is not used.

## Usage

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
DEPLOY_MODE=full ./scripts/aws/deploy_api.sh
```

Replace placeholders in `task-definition.json` before registering:

- `ACCOUNT_ID`, `AWS_REGION`, `fs-REPLACE_EFS_ID`

## CORS

`CORS_ALLOWED_ORIGINS` must include every website origin that embeds the widget (e.g. `https://devweb-agent.mobcoder.ai` for the dev pilot). Match `.env.aws.example` and `deploy/terraform/variables.tf`.

## See also

- [docs/DEVOPS_DEPLOY.md](../../docs/DEVOPS_DEPLOY.md) — DevOps handoff (start here)
- [docs/AWS.md](../../docs/AWS.md) — full ECS/Terraform guide
