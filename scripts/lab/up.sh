#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER="${AADS_CONTROLLER_VM:-aads-controller}"
TARGET="${AADS_TARGET_VM:-aads-target}"
TOKEN_FILE="$ROOT/.aads-lab-token"
ADMIN_KEY_FILE="$ROOT/.aads-lab-admin-key"
INSTALL_TARGET_ALLOY="${AADS_INSTALL_TARGET_ALLOY:-true}"
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
  export AADS_LITELLM_UPSTREAM_API_KEY=...
EOF
  exit 2
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
  openssl rand -hex 32 > "$TOKEN_FILE"
fi
if [[ ! -f "$ADMIN_KEY_FILE" ]]; then
  openssl rand -hex 24 > "$ADMIN_KEY_FILE"
fi

launch_vm() {
  local name="$1"
  if ! multipass info "$name" >/dev/null 2>&1; then
    multipass launch 24.04 --name "$name" --cpus 2 --memory 4G --disk 20G
  fi
}

launch_vm "$CONTROLLER"
launch_vm "$TARGET"

CONTROLLER_IP="$(multipass info "$CONTROLLER" --format json | python3 -c 'import json,sys; d=json.load(sys.stdin)["info"]; print(next(iter(d.values()))["ipv4"][0])')"
TARGET_IP="$(multipass info "$TARGET" --format json | python3 -c 'import json,sys; d=json.load(sys.stdin)["info"]; print(next(iter(d.values()))["ipv4"][0])')"
TOKEN="$(cat "$TOKEN_FILE")"
ADMIN_KEY="$(cat "$ADMIN_KEY_FILE")"

echo "Installing controller Docker dependencies..."
multipass exec "$CONTROLLER" -- sudo apt-get update
multipass exec "$CONTROLLER" -- sudo apt-get install -y docker.io docker-compose-v2 curl jq

echo "Configuring controller Docker DNS..."
multipass exec "$CONTROLLER" -- sudo mkdir -p /etc/docker
multipass exec "$CONTROLLER" -- sudo sh -c \
  "printf '%s\n' '{' '  \"dns\": [\"$DOCKER_DNS_PRIMARY\", \"$DOCKER_DNS_SECONDARY\"]' '}' > /etc/docker/daemon.json"
multipass exec "$CONTROLLER" -- sudo systemctl restart docker

echo "Installing target dependencies..."
multipass exec "$TARGET" -- sudo apt-get update
multipass exec "$TARGET" -- sudo apt-get install -y nginx python3 python3-venv uuid-runtime curl

echo "Copying pi-agent to target..."
COPYFILE_DISABLE=1 tar --exclude='._*' -C "$ROOT" -czf /tmp/aads-pi-agent.tgz pi-agent
multipass transfer /tmp/aads-pi-agent.tgz "$TARGET:/tmp/aads-pi-agent.tgz"
multipass exec "$TARGET" -- bash -lc "rm -rf /tmp/aads-pi-agent && mkdir -p /tmp/aads-pi-agent && tar -xzf /tmp/aads-pi-agent.tgz -C /tmp/aads-pi-agent"
multipass exec "$TARGET" -- sudo env AADS_AGENT_TOKEN="$TOKEN" AADS_AGENT_ENVIRONMENT=test bash /tmp/aads-pi-agent/pi-agent/install/install.sh

echo "Waiting for On-Device Agent facts endpoint..."
FACTS=""
for _ in $(seq 1 30); do
  if FACTS="$(curl -fsS -H "Authorization: Bearer $TOKEN" "http://$TARGET_IP:8090/v1/node/facts" 2>/dev/null)"; then
    break
  fi
  sleep 1
done
if [[ -z "$FACTS" ]]; then
  echo "On-Device Agent did not become ready on http://$TARGET_IP:8090" >&2
  exit 1
fi
TARGET_NODE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])' <<<"$FACTS")"
REGISTRATION_PAYLOAD="$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); d["base_url"]=sys.argv[2]; print(json.dumps(d))' "$FACTS" "http://$TARGET_IP:8090")"

if [[ "$INSTALL_TARGET_ALLOY" == "true" ]]; then
  echo "Installing target Alloy log forwarder..."
  multipass transfer "$ROOT/scripts/lab/install-target-alloy.sh" "$TARGET:/tmp/install-target-alloy.sh"
  multipass exec "$TARGET" -- sudo env \
    AADS_CONTROLLER_LOKI_URL="http://$CONTROLLER_IP:3100" \
    AADS_TARGET_NODE_ID="$TARGET_NODE_ID" \
    bash /tmp/install-target-alloy.sh
fi

echo "Preparing controller .env..."
grep -Ev '^(PI_AGENT_TOKEN|AADS_ADMIN_API_KEY|AADS_NODE_ID|AADS_DEFAULT_NODE_ID|AADS_NODE_ENVIRONMENT|AADS_ENV|AADS_DB_MIGRATION_MODE|LITELLM_MASTER_KEY|LITELLM_MODEL|AADS_LITELLM_UPSTREAM_API_BASE|AADS_LITELLM_UPSTREAM_API_KEY|ENABLE_LOGBERT_FILTER|INITIAL_LOOKBACK_SECONDS|LAYER1_LOKI_QUERY)=' "$ROOT/.env.example" > "$ROOT/.env.lab"
{
  echo "PI_AGENT_TOKEN=$TOKEN"
  echo "AADS_ADMIN_API_KEY=$ADMIN_KEY"
  echo "AADS_NODE_ID=$TARGET_NODE_ID"
  echo "AADS_DEFAULT_NODE_ID=$TARGET_NODE_ID"
  echo "AADS_NODE_ENVIRONMENT=test"
  echo "AADS_ENV=test"
  echo "AADS_DB_MIGRATION_MODE=destructive"
  echo "LITELLM_MASTER_KEY=$LITELLM_MASTER_KEY_VALUE"
  echo "LITELLM_MODEL=$LITELLM_MODEL_VALUE"
  echo "AADS_LITELLM_UPSTREAM_API_BASE=$LITELLM_UPSTREAM_BASE_VALUE"
  echo "AADS_LITELLM_UPSTREAM_API_KEY=$LITELLM_UPSTREAM_KEY_VALUE"
  echo "ENABLE_LOGBERT_FILTER=false"
  echo "INITIAL_LOOKBACK_SECONDS=300"
  echo 'LAYER1_LOKI_QUERY={source="target-nginx"}'
} >> "$ROOT/.env.lab"

cat <<EOF
Controller VM: $CONTROLLER ($CONTROLLER_IP)
Target VM:     $TARGET ($TARGET_IP)
Target node:   $TARGET_NODE_ID
Admin key:     $ADMIN_KEY

Next steps:
1. Copy this repository to the controller VM.
2. Run: docker compose --env-file .env.lab up -d --build
3. Register target:
   curl -H "X-Admin-API-Key: $ADMIN_KEY" -H "Content-Type: application/json" \\
     -d '$REGISTRATION_PAYLOAD' \\
     http://localhost:5000/api/agents/register
EOF
