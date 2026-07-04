resource "aws_secretsmanager_secret" "app" {
  name        = "${local.name_prefix}/app"
  description = "MobCoder sales agent API secrets"
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    OPENAI_API_KEY      = var.openai_api_key != "" ? var.openai_api_key : "REPLACE_ME"
    APIFY_API_TOKEN     = var.apify_api_token != "" ? var.apify_api_token : "REPLACE_ME"
    DATABASE_URL        = "postgresql://${var.db_username}:${urlencode(random_password.db.result)}@${aws_db_instance.main.address}:5432/${var.db_name}"
    REDIS_URL           = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:6379/0"
    HUBSPOT_WEBHOOK_URL = var.hubspot_webhook_url
  })

  depends_on = [
    aws_db_instance.main,
    aws_elasticache_cluster.redis,
  ]
}
