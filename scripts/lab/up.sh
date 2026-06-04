#!/usr/bin/env bash
# Bootstrap the AADS lab on a PVE environment.
# The VMs are assumed to already exist and be accessible via SSH.
#
# Required env:
#   AADS_CONTROLLER_IP           — controller VM IP
#   AADS_TARGET_IP               — target VM IP
#   AADS_LITELLM_UPSTREAM_API_KEY — LLM API key
# Optional env:
#   AADS_SSH_USER                — SSH user on both VMs (default: ubuntu)
#   AADS_SSH_KEY                 — path to private key; omit for ssh-agent
#   AADS_INSTALL_TARGET_ALLOY    — install Alloy on target (default: true)
#   LITELLM_MASTER_KEY / LITELLM_MODEL / AADS_LITELLM_UPSTREAM_API_BASE
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER_IP="${AADS_CONTROLLER_IP:?AADS_CONTROLLER_IP must be set}"
TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"
TOKEN_FILE="$ROOT/.aads-lab-token"
ADMIN_KEY_FILE="$ROOT/.aads-lab-admin-key"
INSTALL_TARGET_ALLOY="${AADS_INSTALL_TARGET_ALLOY:-true}"

if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes"
else
  SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
fi

existing_env_value() {
  local key="$1"
  if [[ -f "$ROOT/.env.lab" ]]; then
    awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ROOT/.env.lab"
  fi
}

LITELLM_MASTER_KEY_VALUE="${LITELLM_MASTER_KEY:-$(existing_env_value LITELLM_MASTER_KEY)}"
LITELLM_MODEL_VALUE="${LITELLM_MODEL:-$(existing_env_value LITELLM_MODEL)}"
LITELLM_UPSTREAM_BASE_VALUE="${AADS_LITELLM_UPSTREAM_API_BASE:-$(existing_env_value AADS_LITELLM_UPSTREAM_API_BASE)}"
LITELLM_UPSTREAM_KEY_VALUE="${AADS_LITELLM_UPSTREAM_API_KEY:-$(existing_env_value AADS_LITELLM_UPSTREAM_API_KEY)}"
DOCKER_DNS_PRIMARY="${AADS_DOCKER_DNS_PRIMARY:-1.1.1.1}"
DOCKER_DNS_SECONDARY="${AADS_DOCKER_DNS_SECONDARY:-8.8.8.8}"

LITELLM_MASTER_KEY_VALUE="${LITELLM_MASTER_KEY_VALUE:-sk-aads-dev}"
LITELLM_MODEL_VALUE="${LITELLM_MODEL_VALUE:-gpt-5.5}"
LITELLM_UPSTREAM_BASE_VALUE="${LITELLM_UPSTREAM_BASE_VALUE:-https://api.openai.com/v1}"

"$ROOT/scripts/lab/check-prereqs.sh"

if [[ -z "$LITELLM_UPSTREAM_KEY_VALUE" || "$LITELLM_UPSTREAM_KEY_VALUE" == "sk-xxxxx" ]]; then
  cat >&2 <<'EOF'
AADS_LITELLM_UPSTREAM_API_KEY is required for the lab System Agent.
Export it before running this script, for example:
  export AADS_LITELLM_UPSTREAM_API_KEY=sk-...
EOF
  exit 2
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
  openssl rand -hex 32 > "$TOKEN_FILE"
fi
if [[ ! -f "$ADMIN_KEY_FILE" ]]; then
  openssl rand -hex 24 > "$ADMIN_KEY_FILE"
fi

TOKEN="$(tr -d '\n' < "$TOKEN_FILE")"
ADMIN_KEY="$(tr -d '\n' < "$ADMIN_KEY_FILE")"

# ── Controller: install Docker ──────────────────────────────────────────────
echo "Installing controller Docker dependencies..."
ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" \
  'sudo apt-get update -qq && sudo apt-get install -y docker.io docker-compose-v2 curl jq'

echo "Configuring controller Docker DNS..."
ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" \
  "sudo mkdir -p /etc/docker && printf '{\\n  \"dns\": [\"${DOCKER_DNS_PRIMARY}\", \"${DOCKER_DNS_SECONDARY}\"]\\n}\\n' | sudo tee /etc/docker/daemon.json > /dev/null && sudo systemctl restart docker"

# ── Target: install system deps + pi-agent ─────────────────────────────────
echo "Installing target system dependencies..."
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" \
  'sudo apt-get update -qq && sudo apt-get install -y nginx python3 python3-venv uuid-runtime curl'

echo "Copying pi-agent to target..."
COPYFILE_DISABLE=1 tar --exclude='._*' -C "$ROOT" -czf /tmp/aads-pi-agent.tgz pi-agent
scp $SSH_OPTS /tmp/aads-pi-agent.tgz "${SSH_USER}@${TARGET_IP}:/tmp/aads-pi-agent.tgz"
rm -f /tmp/aads-pi-agent.tgz
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" \
  'rm -rf /tmp/aads-pi-agent && mkdir -p /tmp/aads-pi-agent && \
   tar -xzf /tmp/aads-pi-agent.tgz -C /tmp/aads-pi-agent && rm /tmp/aads-pi-agent.tgz'
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" \
  "sudo env AADS_AGENT_TOKEN='${TOKEN}' AADS_AGENT_ENVIRONMENT=test \
   bash /tmp/aads-pi-agent/pi-agent/install/install.sh"

# ── Wait for agent + collect facts ─────────────────────────────────────────
echo "Waiting for On-Device Agent facts endpoint..."
FACTS=""
for _ in $(seq 1 30); do
  if FACTS="$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
      "http://${TARGET_IP}:8090/v1/node/facts" 2>/dev/null)"; then
    break
  fi
  sleep 1
done
if [[ -z "$FACTS" ]]; then
  echo "On-Device Agent did not become ready on http://${TARGET_IP}:8090" >&2
  exit 1
fi

TARGET_NODE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])' <<<"$FACTS")"
# Build registration payload with controller-visible base_url.
REGISTRATION_PAYLOAD="$(python3 -c '
import json, sys
facts = json.loads(sys.argv[1])
base_url = sys.argv[2]
# V2: facts carries runner_capabilities, not supported_commands.
payload = {
    "node_id":            facts["node_id"],
    "environment":        facts.get("environment", "test"),
    "agent_version":      facts.get("agent_version", "unknown"),
    "base_url":           base_url,
    "runner_capabilities": facts.get("runner_capabilities", {}),
}
print(json.dumps(payload))
' "$FACTS" "http://${TARGET_IP}:8090")"

# ── Alloy on target ─────────────────────────────────────────────────────────
if [[ "$INSTALL_TARGET_ALLOY" == "true" ]]; then
  echo "Installing target Alloy log forwarder..."
  scp $SSH_OPTS "$ROOT/scripts/lab/install-target-alloy.sh" \
    "${SSH_USER}@${TARGET_IP}:/tmp/install-target-alloy.sh"
  ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" \
    "sudo env AADS_CONTROLLER_LOKI_URL='http://${CONTROLLER_IP}:3100' \
     AADS_TARGET_NODE_ID='${TARGET_NODE_ID}' bash /tmp/install-target-alloy.sh"
fi

# ── Generate .env.lab ───────────────────────────────────────────────────────
echo "Preparing controller .env.lab..."
grep -Ev '^(PI_AGENT_TOKEN|AADS_ADMIN_API_KEY|AADS_NODE_ID|AADS_DEFAULT_NODE_ID|AADS_NODE_ENVIRONMENT|AADS_ENV|AADS_DB_MIGRATION_MODE|LITELLM_MASTER_KEY|LITELLM_MODEL|AADS_LITELLM_UPSTREAM_API_BASE|AADS_LITELLM_UPSTREAM_API_KEY|ENABLE_LOGBERT_FILTER|INITIAL_LOOKBACK_SECONDS|LAYER1_LOKI_QUERY)=' "$ROOT/.env.example" > "$ROOT/.env.lab"
{
  echo "PI_AGENT_TOKEN=${TOKEN}"
  echo "AADS_ADMIN_API_KEY=${ADMIN_KEY}"
  echo "AADS_NODE_ID=${TARGET_NODE_ID}"
  echo "AADS_DEFAULT_NODE_ID=${TARGET_NODE_ID}"
  echo "AADS_NODE_ENVIRONMENT=test"
  echo "AADS_ENV=test"
  echo "AADS_DB_MIGRATION_MODE=destructive"
  echo "LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY_VALUE}"
  echo "LITELLM_MODEL=${LITELLM_MODEL_VALUE}"
  echo "AADS_LITELLM_UPSTREAM_API_BASE=${LITELLM_UPSTREAM_BASE_VALUE}"
  echo "AADS_LITELLM_UPSTREAM_API_KEY=${LITELLM_UPSTREAM_KEY_VALUE}"
  echo "ENABLE_LOGBERT_FILTER=false"
  echo "INITIAL_LOOKBACK_SECONDS=300"
  echo 'LAYER1_LOKI_QUERY={source="target-nginx"}'
} >> "$ROOT/.env.lab"

cat <<EOF

Controller VM: ${CONTROLLER_IP}
Target VM:     ${TARGET_IP}
Target node:   ${TARGET_NODE_ID}
Admin key:     ${ADMIN_KEY}

Next steps:
1. Sync the repo to the controller:
     AADS_CONTROLLER_IP=${CONTROLLER_IP} scripts/lab/sync-controller.sh
2. SSH into the controller and start services:
     ssh ${SSH_USER}@${CONTROLLER_IP}
     cd ~/AADS && COMPOSE_BAKE=false sudo -E docker compose --env-file .env.lab up -d --build
3. Register target node:
     curl -H "X-Admin-API-Key: ${ADMIN_KEY}" \\
          -H "Content-Type: application/json" \\
          -d '${REGISTRATION_PAYLOAD}' \\
          http://${CONTROLLER_IP}:5000/api/agents/register
EOF
