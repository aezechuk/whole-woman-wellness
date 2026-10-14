#!/bin/bash
# Pull the latest code, refresh secrets from Parameter Store, rebuild, migrate.
# Safe to re-run. Run as root on the server (Session Manager: sudo bash deploy.sh).
set -euo pipefail
REGION=us-east-2
APP=/opt/wellness/app
ENV_FILE=/opt/wellness/.env
cd "$APP"

TOKEN=$(aws ssm get-parameter --region $REGION --name /wellness/GITHUB_TOKEN --with-decryption --query Parameter.Value --output text)
REPO=$(git remote get-url origin)
git -c credential.helper= fetch --quiet "https://x-access-token:${TOKEN}@${REPO#https://}" main
git reset --hard FETCH_HEAD
unset TOKEN

# App settings: everything under /wellness/ except values the app must never see.
umask 077
aws ssm get-parameters-by-path --region $REGION --path /wellness/ --with-decryption \
  --query 'Parameters[].[Name,Value]' --output text \
  | awk -F'\t' '{ sub("^/wellness/", "", $1); if ($1 != "GITHUB_TOKEN" && $1 != "REPO_URL") print $1 "=" $2 }' \
  > "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
chmod 600 "$ENV_FILE"

cd "$APP/deploy"
COMPOSE="docker compose --env-file $ENV_FILE"
$COMPOSE build app
$COMPOSE up -d db
$COMPOSE run --rm app flask db upgrade
$COMPOSE up -d --remove-orphans
$COMPOSE up -d --no-deps --force-recreate app   # pick up changed settings in .env
docker image prune -f >/dev/null

install -m 0644 "$APP/deploy/scripts/wellness.cron" /etc/cron.d/wellness
echo "Deployed $(git -C "$APP" rev-parse --short HEAD) at $(date -u +%FT%TZ)"
