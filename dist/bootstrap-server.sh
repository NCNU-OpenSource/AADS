#!/usr/bin/env bash
# ============================================================================
# AADS Server — one-shot bootstrap (Docker Compose, registry images)
#
# Run on the server/controller host from a repo checkout or the server bundle:
#   bash dist/bootstrap-server.sh
#
# Generates .env (interactive secrets), pulls the prebuilt service images,
# brings the whole stack up, verifies the database, and prints the exact
# one-line command to install the On-Device Agent on each target.
#
# Pre-answer any prompt with an environment variable:
#   AADS_LITELLM_UPSTREAM_API_KEY  upstream LLM API key            [required]
#   LITELLM_MODEL                  model name (default: gpt-5.5)
#   AADS_LITELLM_UPSTREAM_API_BASE default: https://api.openai.com/v1
#   PI_AGENT_TOKEN                 agent bearer token (default: auto-generated)
#   AADS_ADMIN_API_KEY             dashboard admin key (default: auto-generated)
#   TIMESCALEDB_PASSWORD           DB password (default: auto-generated)
#   LITELLM_MASTER_KEY             in-stack proxy key (default: auto-generated)
#   AADS_SERVER_IP                 IP agents use to reach this server (auto)
#   AADS_IMAGE_REGISTRY            default: ghcr.io/bs10081
#   AADS_IMAGE_TAG                 default: latest
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/docker-compose.prod.yaml"
ENV_FILE="$ROOT/.env"
ENV_EXAMPLE="$ROOT/.env.example"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

prompt() {
  local var="$1" message="$2" default="${3:-}" secret="${4:-}"
  [[ -n "${!var:-}" ]] && return 0
  if [[ ! -r /dev/tty ]]; then
    [[ -n "$default" ]] && { printf -v "$var" '%s' "$default"; return 0; }
    die "$var is required but no terminal is available (set $var in the environment)."
  fi
  local reply
  if [[ -n "$secret" ]]; then
    read -r -s -p "$message: " reply < /dev/tty; echo >/dev/tty
    reply="${reply:-$default}"   # empty input falls back to default (e.g. auto-generated secret)
  elif [[ -n "$default" ]]; then
    read -r -p "$message [$default]: " reply < /dev/tty; reply="${reply:-$default}"
  else
    read -r -p "$message: " reply < /dev/tty
  fi
  printf -v "$var" '%s' "$reply"
}

gen_secret() { openssl rand -hex "${1:-32}"; }

detect_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "$ip"
}

[[ -f "$COMPOSE_FILE" ]] || die "Missing $COMPOSE_FILE — run from a full server bundle / repo checkout."
[[ -f "$ENV_EXAMPLE" ]] || die "Missing $ENV_EXAMPLE."

# ── Docker ──────────────────────────────────────────────────────────────────
log "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker not found."
  prompt INSTALL_DOCKER "Install Docker now via apt? (y/n)" "y"
  [[ "${INSTALL_DOCKER,,}" == y* ]] || die "Docker is required."
  sudo apt-get update -qq
  sudo apt-get install -y docker.io docker-compose-v2 curl jq
fi
docker compose version >/dev/null 2>&1 || die "'docker compose' v2 plugin not available."
DOCKER="docker"; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
ok "Docker ready ($DOCKER)"

AADS_IMAGE_REGISTRY="${AADS_IMAGE_REGISTRY:-ghcr.io/bs10081}"
AADS_IMAGE_TAG="${AADS_IMAGE_TAG:-latest}"

# ── Secrets / config ────────────────────────────────────────────────────────
log "Collecting configuration"
prompt AADS_LITELLM_UPSTREAM_API_KEY "Upstream LLM API key (e.g. sk-...)" "" secret
[[ -n "${AADS_LITELLM_UPSTREAM_API_KEY:-}" && "$AADS_LITELLM_UPSTREAM_API_KEY" != "sk-xxxxx" ]] \
  || die "A real upstream LLM API key is required."
prompt LITELLM_MODEL "LLM model" "gpt-5.5"
prompt AADS_LITELLM_UPSTREAM_API_BASE "LLM upstream base URL" "https://api.openai.com/v1"
prompt PI_AGENT_TOKEN "Agent token (blank = auto-generate)" "$(gen_secret 32)" secret
prompt AADS_ADMIN_API_KEY "Dashboard admin key (blank = auto-generate)" "$(gen_secret 24)" secret
prompt TIMESCALEDB_PASSWORD "TimescaleDB password (blank = auto-generate)" "$(gen_secret 16)" secret
prompt LITELLM_MASTER_KEY "In-stack LiteLLM key (blank = auto-generate)" "sk-$(gen_secret 16)" secret

if [[ -z "${AADS_SERVER_IP:-}" ]]; then
  prompt AADS_SERVER_IP "IP/host agents use to reach THIS server" "$(detect_ip)"
fi
[[ -n "${AADS_SERVER_IP:-}" ]] || die "AADS_SERVER_IP is required (could not auto-detect)."

# ── Write .env ──────────────────────────────────────────────────────────────
log "Writing $ENV_FILE"
OVERRIDE_KEYS='TIMESCALEDB_PASSWORD|PI_AGENT_TOKEN|AADS_ADMIN_API_KEY|LITELLM_MASTER_KEY|LITELLM_MODEL|AADS_LITELLM_UPSTREAM_API_BASE|AADS_LITELLM_UPSTREAM_API_KEY|SLACK_WEBHOOK_URL|AADS_IMAGE_REGISTRY|AADS_IMAGE_TAG'
grep -Ev "^(${OVERRIDE_KEYS})=" "$ENV_EXAMPLE" > "$ENV_FILE"
{
  echo ""
  echo "# ---- generated by dist/bootstrap-server.sh ----"
  echo "TIMESCALEDB_PASSWORD=${TIMESCALEDB_PASSWORD}"
  echo "PI_AGENT_TOKEN=${PI_AGENT_TOKEN}"
  echo "AADS_ADMIN_API_KEY=${AADS_ADMIN_API_KEY}"
  echo "LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}"
  echo "LITELLM_MODEL=${LITELLM_MODEL}"
  echo "AADS_LITELLM_UPSTREAM_API_BASE=${AADS_LITELLM_UPSTREAM_API_BASE}"
  echo "AADS_LITELLM_UPSTREAM_API_KEY=${AADS_LITELLM_UPSTREAM_API_KEY}"
  echo "SLACK_WEBHOOK_URL="
  echo "AADS_IMAGE_REGISTRY=${AADS_IMAGE_REGISTRY}"
  echo "AADS_IMAGE_TAG=${AADS_IMAGE_TAG}"
} >> "$ENV_FILE"
chmod 0600 "$ENV_FILE"
ok ".env written (secrets kept at mode 0600)"

# ── Build the agent payload the dashboard will serve ────────────────────────
log "Building agent payload for self-serve install"
bash "$ROOT/dist/build-agent-payload.sh" "$ROOT/dist/aads-agent.tgz"
ok "dist/aads-agent.tgz ready (served at http://${AADS_SERVER_IP}:5000/aads-agent.tgz)"

# ── Pull images + start ─────────────────────────────────────────────────────
log "Pulling service images ($AADS_IMAGE_REGISTRY :$AADS_IMAGE_TAG)"
if ! $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull; then
  warn "Image pull failed. If the registry is private, authenticate first:"
  warn "  echo \$GHCR_TOKEN | $DOCKER login ghcr.io -u <github-user> --password-stdin"
  die "Resolve the pull error and re-run this script."
fi
log "Starting the stack"
$DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
ok "Containers started"

# ── Verify database ─────────────────────────────────────────────────────────
log "Waiting for TimescaleDB migrations"
TABLES=0
for _ in $(seq 1 30); do
  TABLES="$($DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" exec -T timescaledb \
    psql -U logdb -d logdb -At -c \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo 0)"
  [[ "${TABLES:-0}" =~ ^[0-9]+$ && "$TABLES" -ge 10 ]] && break
  sleep 3
done
if [[ "${TABLES:-0}" =~ ^[0-9]+$ && "$TABLES" -ge 10 ]]; then
  ok "Database ready ($TABLES tables)"
else
  warn "Expected ≥10 tables, found '${TABLES}'. Check: $DOCKER compose logs timescaledb"
fi

# ── Summary + agent install command ─────────────────────────────────────────
cat <<EOF

============================================================================
 AADS server is up.

   Dashboard : http://${AADS_SERVER_IP}:5000
   Grafana   : http://${AADS_SERVER_IP}:3000
   Admin key : ${AADS_ADMIN_API_KEY}

 Install the On-Device Agent on each target (copy-paste on the target host):

   curl -fsSL http://${AADS_SERVER_IP}:5000/install-agent.sh | sudo \\
     AADS_SERVER=${AADS_SERVER_IP} \\
     AADS_AGENT_TOKEN=${PI_AGENT_TOKEN} \\
     AADS_ADMIN_API_KEY=${AADS_ADMIN_API_KEY} bash

 Note: AADS_DEFAULT_NODE_ID in .env is a fallback only — Alloy tags logs with
 each node's real id, so plans target the right node automatically.
============================================================================
EOF
