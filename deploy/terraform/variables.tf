variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short project name used in resource names"
  type        = string
  default     = "mobcoder-sales-agent"
}

variable "environment" {
  description = "Environment label: prod (locked RDS, docs off), staging or dev (docs on, easier teardown)"
  type        = string
  default     = "prod"

  validation {
    condition     = contains(["prod", "staging", "dev"], var.environment)
    error_message = "environment must be prod, staging, or dev."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "cache_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

variable "ecs_cpu" {
  type    = number
  default = 1024
}

variable "ecs_memory" {
  type    = number
  default = 2048
}

variable "ecs_desired_count" {
  type    = number
  default = 2
}

variable "cors_allowed_origins" {
  type    = string
  default = "https://devweb-agent.mobcoder.ai,https://mobcoder.ai,https://www.mobcoder.ai"
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for HTTPS on the ALB. Leave empty for HTTP-only (staging)."
  type        = string
  default     = ""
}

variable "openai_api_key" {
  description = "OpenAI API key (stored in Secrets Manager). Leave empty to set manually after apply."
  type        = string
  sensitive   = true
  default     = ""
}

variable "apify_api_token" {
  description = "Apify token (optional, stored in Secrets Manager)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "hubspot_webhook_url" {
  type      = string
  sensitive = true
  default   = ""
}

variable "github_repo" {
  description = "GitHub repo org/name for OIDC deploy role (e.g. mobcoder/mobcoder-sales-agent)"
  type        = string
  default     = ""
}

variable "enable_github_oidc" {
  description = "Create IAM role for GitHub Actions OIDC deploy"
  type        = bool
  default     = false
}

variable "auto_ingest_on_start" {
  description = "Set true on first deploy until vector_store_count > 0, then set false"
  type        = bool
  default     = true
}

variable "db_name" {
  type    = string
  default = "mobcoder"
}

variable "db_username" {
  type    = string
  default = "mobcoder"
}
