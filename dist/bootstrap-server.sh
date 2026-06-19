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
#   AADS_IMAGE_REGISTRY            default: ghcr.io/ncnu-opensource
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

# Harden the daemon for large image pulls over unstable links. Some service
# images (PyTorch-based) are multi-GB; the default 5 blob-download attempts
# can be exhausted by transient CDN EOFs. Bump to 10. Merge into any existing
# daemon.json (e.g. DNS settings) rather than clobbering it.
tune_docker_daemon() {
  local djson=/etc/docker/daemon.json current merged
  command -v jq >/dev/null 2>&1 || { sudo apt-get install -y jq >/dev/null 2>&1 || return 0; }
  current="$(sudo cat "$djson" 2>/dev/null || echo '{}')"
  echo "$current" | jq -e '.["max-download-attempts"] == 10' >/dev/null 2>&1 && return 0
  merged="$(echo "$current" | jq '. + {"max-download-attempts": 10}')" || return 0
  echo "$merged" | sudo tee "$djson" >/dev/null
  sudo systemctl restart docker 2>/dev/null || sudo service docker restart 2>/dev/null || true
  sleep 3
  ok "Docker daemon tuned (max-download-attempts=10)"
}
tune_docker_daemon

AADS_IMAGE_REGISTRY="${AADS_IMAGE_REGISTRY:-ghcr.io/ncnu-opensource}"
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

# ── Pull images + start (retry on network errors; fallback to build if private) ──
PULL_ERR="$(mktemp)"
cleanup_pull() { rm -f "$PULL_ERR"; }
trap cleanup_pull EXIT

PULL_OK=false
MAX_PULL_ATTEMPTS=8
# Serialize blob downloads: on an unstable link, one image at a time finishes
# each large blob inside the connection's stable window instead of splitting
# bandwidth across many concurrent (and individually slower) transfers.
export COMPOSE_PARALLEL_LIMIT=1
log "Pulling service images ($AADS_IMAGE_REGISTRY :$AADS_IMAGE_TAG, serialized)"
for attempt in $(seq 1 "$MAX_PULL_ATTEMPTS"); do
  if $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" pull 2>"$PULL_ERR"; then
    PULL_OK=true
    break
  fi
  # Auth errors won't be fixed by retrying — break immediately.
  if grep -qi "unauthorized\|denied\|authentication required" "$PULL_ERR"; then
    break
  fi
  warn "Pull attempt $attempt/$MAX_PULL_ATTEMPTS failed (network error) — retrying in 5s"
  warn "$(tail -1 "$PULL_ERR")"
  sleep 5
done

if $PULL_OK; then
  log "Starting the stack"
  $DOCKER compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d
  ok "Containers started"
else
  # GHCR packages default to private even on public repos. Fall back to
  # building locally from a shallow clone of the public repository.
  if grep -qi "unauthorized\|denied\|authentication required" "$PULL_ERR"; then
    warn "GHCR pull unauthorized — packages are private by default."
    warn "Falling back to local build from the public repository (takes ~5 min)."
    warn "To skip this next time: make packages public at https://github.com/orgs/NCNU-OpenSource/packages"

    SRC_DIR="$(mktemp -d /tmp/aads-src.XXXXXX)"
    cleanup_src() { rm -rf "$SRC_DIR"; }
    trap cleanup_src EXIT

    log "Cloning public repository"
    command -v git >/dev/null 2>&1 || { sudo apt-get install -y git >/dev/null; }
    git clone --depth 1 https://github.com/NCNU-OpenSource/AADS.git "$SRC_DIR"

    # Overlay our already-generated .env and config dirs from the bundle
    cp "$ENV_FILE" "$SRC_DIR/.env"

    log "Building images locally"
    $DOCKER compose -f "$SRC_DIR/docker-compose.yaml" --env-file "$SRC_DIR/.env" \
      up -d --build
    ok "Containers started (built locally)"

    # Point COMPOSE_FILE at the dev compose so the DB check below works
    COMPOSE_FILE="$SRC_DIR/docker-compose.yaml"
    ENV_FILE="$SRC_DIR/.env"
  else
    cat "$PULL_ERR" >&2
    die "Image pull failed (see error above). Fix the issue and re-run."
  fi
fi

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
GITHUB_RELEASE_BASE="https://github.com/NCNU-OpenSource/AADS/releases/latest/download"

cat <<EOF

============================================================================
 AADS server is up.

   Dashboard : http://${AADS_SERVER_IP}:5000
   Grafana   : http://${AADS_SERVER_IP}:3000
   Admin key : ${AADS_ADMIN_API_KEY}

 ── Install On-Device Agent (HTTPS, public GitHub — no server dependency) ──

   curl -fsSL ${GITHUB_RELEASE_BASE}/install-agent.sh | sudo \\
     AADS_SERVER=${AADS_SERVER_IP} \\
     AADS_AGENT_TOKEN=${PI_AGENT_TOKEN} \\
     AADS_ADMIN_API_KEY=${AADS_ADMIN_API_KEY} \\
     AADS_RELEASE_BASE_URL=${GITHUB_RELEASE_BASE} bash

 ── Install On-Device Agent (HTTP, self-hosted — air-gapped friendly) ──────

   curl -fsSL http://${AADS_SERVER_IP}:5000/install-agent.sh | sudo \\
     AADS_SERVER=${AADS_SERVER_IP} \\
     AADS_AGENT_TOKEN=${PI_AGENT_TOKEN} \\
     AADS_ADMIN_API_KEY=${AADS_ADMIN_API_KEY} bash

============================================================================
EOF
