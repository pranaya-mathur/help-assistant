locals {
  name_prefix = "${var.project_name}-${var.environment}"

  is_production = var.environment == "prod"
  # Staging/dev: expose Swagger and debug endpoints for QA (still APP_ENV=production on AWS).
  enable_dev_surface = !local.is_production

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
  }

  container_image = "${aws_ecr_repository.api.repository_url}:latest"

  container_environment = [
    { name = "APP_ENV", value = "production" },
    { name = "API_PORT", value = "8080" },
    { name = "API_RELOAD", value = "false" },
    { name = "AUTO_INGEST_ON_START", value = var.auto_ingest_on_start ? "true" : "false" },
    { name = "UVICORN_WORKERS", value = "1" },
    { name = "SESSION_STORE_BACKEND", value = "postgres" },
    { name = "AUTO_MIGRATE_DB", value = "true" },
    { name = "PERSIST_ANALYTICS_EVENTS", value = "true" },
    { name = "RATE_LIMIT_BACKEND", value = "redis" },
    { name = "TRUST_PROXY_HEADERS", value = "true" },
    { name = "FORWARDED_ALLOW_IPS", value = "*" },
    { name = "ENABLE_DOCS", value = local.enable_dev_surface ? "true" : "false" },
    { name = "ENABLE_SOURCE_ENDPOINTS", value = local.enable_dev_surface ? "true" : "false" },
    { name = "ENABLE_DEBUG_ENDPOINTS", value = local.enable_dev_surface ? "true" : "false" },
    { name = "EXPOSE_INTERNAL_SALES_METADATA", value = local.enable_dev_surface ? "true" : "false" },
    { name = "ENABLE_LEAD_QUALIFICATION", value = "true" },
    { name = "ENABLE_CHAT_STREAMING", value = "true" },
    { name = "ENABLE_LLM_GROUNDING", value = "true" },
    { name = "GROUNDING_PROVIDER", value = "heuristic" },
    { name = "CHROMA_PERSIST_DIR", value = "/mnt/efs/chroma" },
    { name = "BM25_INDEX_PATH", value = "/mnt/efs/indexes/bm25_index.pkl" },
    { name = "HYBRID_RETRIEVAL_ENABLED", value = "false" },
    { name = "RERANKER_TOP_K", value = "6" },
    { name = "CORS_ALLOWED_ORIGINS", value = var.cors_allowed_origins },
  ]

  container_secrets = [
    { name = "OPENAI_API_KEY", valueFrom = "${aws_secretsmanager_secret.app.arn}:OPENAI_API_KEY::" },
    { name = "DATABASE_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:DATABASE_URL::" },
    { name = "REDIS_URL", valueFrom = "${aws_secretsmanager_secret.app.arn}:REDIS_URL::" },
    { name = "APIFY_API_TOKEN", valueFrom = "${aws_secretsmanager_secret.app.arn}:APIFY_API_TOKEN::" },
  ]
}

resource "random_password" "db" {
  length  = 24
  special = false
}
