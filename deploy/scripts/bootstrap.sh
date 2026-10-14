#!/bin/bash
# EC2 first-boot setup (Amazon Linux 2023, arm64). Installs Docker, enables
# automatic security updates, then deploys if secrets are already in place.
set -euo pipefail
exec > >(tee -a /var/log/wellness-bootstrap.log) 2>&1

dnf -y upgrade --security
dnf -y install docker git cronie dnf-automatic
systemctl enable --now docker crond

# Docker Compose plugin (not packaged on AL2023)
COMPOSE_VERSION=v2.29.7
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-aarch64" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod 0755 /usr/local/lib/docker/cli-plugins/docker-compose

# Apply security updates automatically every day
sed -i 's/^upgrade_type.*/upgrade_type = security/; s/^apply_updates.*/apply_updates = yes/' /etc/dnf/automatic.conf
systemctl enable --now dnf-automatic.timer

install -d -m 0700 /opt/wellness

# Deploy now if the secrets exist; otherwise run deploy.sh later via Session Manager.
if aws ssm get-parameter --region us-east-1 --name /wellness/GITHUB_TOKEN >/dev/null 2>&1; then
  TOKEN=$(aws ssm get-parameter --region us-east-1 --name /wellness/GITHUB_TOKEN --with-decryption --query Parameter.Value --output text)
  REPO=$(aws ssm get-parameter --region us-east-1 --name /wellness/REPO_URL --query Parameter.Value --output text)
  git clone "https://x-access-token:${TOKEN}@${REPO#https://}" /opt/wellness/app
  git -C /opt/wellness/app remote set-url origin "$REPO"
  bash /opt/wellness/app/deploy/scripts/deploy.sh
fi
