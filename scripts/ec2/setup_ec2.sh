#!/usr/bin/env bash
# One-time EC2 bootstrap (Amazon Linux 2023 or Ubuntu 22.04+).
# Run as root or with sudo on a fresh instance:
#   curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/mobcoder.ai-agent/main/scripts/ec2/setup_ec2.sh | sudo bash
# Or from a cloned repo:
#   sudo ./scripts/ec2/setup_ec2.sh
#
# After setup:
#   1. Attach IAM role with AmazonEC2ContainerRegistryReadOnly (+ optional SSM)
#   2. Security group: 22 (your IP), 80/443 (public or ALB)
#   3. Clone repo to /opt/mobcoder (or set APP_DIR)
#   4. cp .env.ec2.example .env.production && edit OPENAI_API_KEY etc.
#   5. First deploy via GitHub Actions or manual ./scripts/ec2/deploy.sh

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mobcoder}"
DEPLOY_USER="${DEPLOY_USER:-ec2-user}"

if [ "$(id -u)" -ne 0 ]; then
  echo "[setup] Run as root: sudo $0"
  exit 1
fi

echo "[setup] Installing Docker..."
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl git awscli python3
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  DEPLOY_USER="${DEPLOY_USER:-ubuntu}"
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y docker git awscli python3
  systemctl enable --now docker
  DEPLOY_USER="${DEPLOY_USER:-ec2-user}"
  mkdir -p /usr/local/lib/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$(uname -m)" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
else
  echo "[setup] Unsupported OS — install Docker + Compose manually."
  exit 1
fi

systemctl enable --now docker 2>/dev/null || true
usermod -aG docker "${DEPLOY_USER}" 2>/dev/null || true

echo "[setup] Creating app directory ${APP_DIR}..."
mkdir -p "${APP_DIR}"
chown "${DEPLOY_USER}:${DEPLOY_USER}" "${APP_DIR}"

echo "[setup] Done."
echo ""
echo "Next steps:"
echo "  1. Attach IAM instance profile: AmazonEC2ContainerRegistryReadOnly"
echo "  2. As ${DEPLOY_USER}: git clone <repo> ${APP_DIR}"
echo "  3. cd ${APP_DIR} && cp .env.ec2.example .env.production  # fill secrets"
echo "  4. Add GitHub secrets (EC2_HOST, EC2_USER, EC2_SSH_PRIVATE_KEY, AWS_REGION, ECR push creds)"
echo "  5. Push to main or run workflow_dispatch — deploy-ec2.yml"
