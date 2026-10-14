#!/bin/bash
# Run once from your own machine or AWS CloudShell (signed in as an admin).
# Generates the app's secrets and stores them as SecureString parameters.
# Existing values are never overwritten (rotating the DB password would lock out the app).
set -euo pipefail
REGION=us-east-1

put_if_missing() {
  local name=$1 value=$2
  if aws ssm get-parameter --region $REGION --name "$name" >/dev/null 2>&1; then
    echo "exists: $name (left unchanged)"
  else
    aws ssm put-parameter --region $REGION --name "$name" --type SecureString --value "$value" >/dev/null
    echo "created: $name"
  fi
}

put_if_missing /wellness/SECRET_KEY "$(openssl rand -base64 48 | tr -d '\n')"
put_if_missing /wellness/POSTGRES_PASSWORD "$(openssl rand -hex 32)"

if ! aws ssm get-parameter --region $REGION --name /wellness/GITHUB_TOKEN >/dev/null 2>&1; then
  read -r -s -p "Paste the read-only GitHub token (input hidden): " TOKEN; echo
  put_if_missing /wellness/GITHUB_TOKEN "$TOKEN"
fi
