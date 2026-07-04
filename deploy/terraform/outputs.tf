data "aws_caller_identity" "current" {}

output "aws_region" {
  value = var.aws_region
}

output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  value = aws_ecs_service.api.name
}

output "alb_dns_name" {
  value = aws_lb.api.dns_name
}

output "api_health_url" {
  value = "http://${aws_lb.api.dns_name}/api/v1/health"
}

output "secrets_manager_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "github_deploy_role_arn" {
  value = var.enable_github_oidc ? aws_iam_role.github_deploy[0].arn : ""
}

output "deploy_commands" {
  value = <<-EOT
    # 1. Push first image (required before ECS tasks become healthy):
    aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.api.repository_url}
    docker build -t ${aws_ecr_repository.api.repository_url}:latest .
    docker push ${aws_ecr_repository.api.repository_url}:latest

    # 2. Force new deployment:
    aws ecs update-service --cluster ${aws_ecs_cluster.main.name} --service ${aws_ecs_service.api.name} --force-new-deployment --region ${var.aws_region}

    # 3. Smoke test:
    API_URL=http://${aws_lb.api.dns_name} ./scripts/aws/smoke_health.sh
  EOT
}

output "next_steps" {
  value = <<-EOT
    1. Update Secrets Manager if OPENAI_API_KEY was REPLACE_ME: ${aws_secretsmanager_secret.app.name}
    2. Push Docker image (see deploy_commands output)
    3. Point widget at: http://${aws_lb.api.dns_name}/api/v1/chat (or HTTPS after ACM cert)
    4. After health shows vector_store_count > 0, set auto_ingest_on_start = false and re-apply
  EOT
}
