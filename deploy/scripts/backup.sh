#!/bin/bash
# Nightly database backup to S3 (write-only bucket, encrypted, auto-deleted after 14 days).
set -euo pipefail
ENV_FILE=/opt/wellness/.env
BUCKET=$(grep '^BACKUP_BUCKET=' "$ENV_FILE" | cut -d= -f2-)
STAMP=$(date -u +%Y-%m-%dT%H%MZ)
cd /opt/wellness/app/deploy
docker compose --env-file "$ENV_FILE" exec -T db pg_dump -U wellness -d wellness --no-owner \
  | gzip -9 \
  | aws s3 cp - "s3://${BUCKET}/db/${STAMP}.sql.gz" --region us-east-1 --only-show-errors
echo "backup ok ${STAMP}"
