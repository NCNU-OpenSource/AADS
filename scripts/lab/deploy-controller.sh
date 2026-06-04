#!/usr/bin/env bash
# Build and restart a controller service (default: layer2-analyzer) via SSH.
# PVE environment: set AADS_CONTROLLER_IP before running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER_IP="${AADS_CONTROLLER_IP:?AADS_CONTROLLER_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
SERVICE="${AADS_CONTROLLER_SERVICE:-layer2-analyzer}"

"$ROOT/scripts/lab/check-prereqs.sh"

if [[ ! -f "$ROOT/.env.lab" ]]; then
  echo "ERROR: missing $ROOT/.env.lab; run scripts/lab/up.sh first." >&2
  exit 1
fi

"$ROOT/scripts/lab/sync-controller.sh"

ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" \
  "cd ~/AADS && COMPOSE_BAKE=false sudo -E docker compose --env-file .env.lab build '${SERVICE}' && \
   COMPOSE_BAKE=false sudo -E docker compose --env-file .env.lab up -d '${SERVICE}'"

echo "Controller service deployed on ${CONTROLLER_IP}: ${SERVICE}"
