#!/bin/bash
# Create the program cohort with its join code. Run once on the server: sudo bash create-cohort.sh WIARTS26
set -euo pipefail
CODE=${1:?usage: create-cohort.sh JOINCODE}
cd /opt/wellness/app/deploy
docker compose --env-file /opt/wellness/.env exec -T app flask create-cohort --name "WiARTS 2026 Whole Woman Wellness" --join-code "$CODE"
