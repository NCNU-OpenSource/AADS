#!/usr/bin/env bash
# ============================================================================
# AADS On-Device Agent — one-shot installer
#
#   curl -fsSL http://<server>:5000/install-agent.sh | sudo bash
#
# Installs the On-Device Agent (V2 runner), registers the node with the AADS
# server, and installs the Alloy log forwarder — in one interactive run.
#
# Every prompt can be pre-answered with an environment variable so the same
# script works non-interactively (CI / automation):
#
#   AADS_SERVER            controller host/IP (e.g. 100.72.172.83)   [prompted]
#   AADS_AGENT_TOKEN       bearer token shared with the server       [prompted]
#   AADS_ADMIN_API_KEY     admin key, used once to register the node [prompted]
#   AADS_AGENT_ENVIRONMENT test | prod (default: test)
#   AADS_AGENT_BASE_URL    URL the server uses to reach this agent
#                          (default: http://<auto-detected-ip>:8090)
#   AADS_DASHBOARD_URL     default: http://<AADS_SERVER>:5000
#   AADS_LOKI_URL          default: http://<AADS_SERVER>:3100
#   AADS_RELEASE_BASE_URL  where to fetch aads-agent.tgz from
#                          (default: <AADS_DASHBOARD_URL>)
#   AADS_INSTALL_ALLOY     install Alloy log forwarder (default: true)
#   AADS_SKIP_REGISTER     skip node registration (default: false)
# ============================================================================
set -euo pipefail

AGENT_PORT=8090
TMP_DIR=""
cleanup() { [[ -n "$TMP_DIR" && -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# Read a value: use the env var if already set, otherwise prompt on /dev/tty.
# `curl | bash` leaves stdin pointing at the script, so prompts must use the
# terminal directly. -s hides input (for secrets).
prompt() {
  local var="$1" message="$2" default="${3:-}" secret="${4:-}"
  local current="${!var:-}"
  if [[ -n "$current" ]]; then return 0; fi
  if [[ ! -r /dev/tty ]]; then
    [[ -n "$default" ]] && { printf -v "$var" '%s' "$default"; return 0; }
    die "$var is required but no terminal is available (set $var in the environment)."
  fi
  local reply
  if [[ -n "$secret" ]]; then
    read -r -s -p "$message: " reply < /dev/tty; echo >/dev/tty
  elif [[ -n "$default" ]]; then
    read -r -p "$message [$default]: " reply < /dev/tty
    reply="${reply:-$default}"
  else
    read -r -p "$message: " reply < /dev/tty
  fi
  printf -v "$var" '%s' "$reply"
}

# Best-effort detection of the IP the server will use to reach this node.
detect_ip() {
  local ip
  ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  [[ -z "$ip" ]] && ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  echo "$ip"
}

[[ "${EUID}" -eq 0 ]] || die "Run as root (use: curl -fsSL <url> | sudo bash)."
command -v apt-get >/dev/null 2>&1 || die "This installer targets Debian/Ubuntu (apt-get not found)."

# ── Gather configuration ────────────────────────────────────────────────────
log "AADS On-Device Agent installer"
prompt AADS_SERVER "Server / Controller address (host or IP)"
[[ -n "${AADS_SERVER:-}" ]] || die "AADS_SERVER is required."
prompt AADS_AGENT_TOKEN "Agent token (from the server bootstrap output)" "" secret
[[ -n "${AADS_AGENT_TOKEN:-}" ]] || die "AADS_AGENT_TOKEN is required."

AADS_AGENT_ENVIRONMENT="${AADS_AGENT_ENVIRONMENT:-test}"
AADS_DASHBOARD_URL="${AADS_DASHBOARD_URL:-http://${AADS_SERVER}:5000}"
AADS_LOKI_URL="${AADS_LOKI_URL:-http://${AADS_SERVER}:3100}"
AADS_RELEASE_BASE_URL="${AADS_RELEASE_BASE_URL:-$AADS_DASHBOARD_URL}"
AADS_INSTALL_ALLOY="${AADS_INSTALL_ALLOY:-true}"
AADS_SKIP_REGISTER="${AADS_SKIP_REGISTER:-false}"

if [[ "$AADS_SKIP_REGISTER" != "true" ]]; then
  prompt AADS_ADMIN_API_KEY "Admin API key (used once to register this node)" "" secret
  [[ -n "${AADS_ADMIN_API_KEY:-}" ]] || die "AADS_ADMIN_API_KEY is required (or set AADS_SKIP_REGISTER=true)."
fi

# base_url the server will call back on. Auto-detect, then confirm/override.
if [[ -z "${AADS_AGENT_BASE_URL:-}" ]]; then
  detected="$(detect_ip)"
  default_base="http://${detected:-CHANGE_ME}:${AGENT_PORT}"
  prompt AADS_AGENT_BASE_URL "URL the server uses to reach THIS node" "$default_base"
fi
[[ "$AADS_AGENT_BASE_URL" == *CHANGE_ME* ]] && die "Could not auto-detect this node's IP; set AADS_AGENT_BASE_URL explicitly."

log "Configuration"
echo "  server          : $AADS_SERVER"
echo "  dashboard        : $AADS_DASHBOARD_URL"
echo "  loki             : $AADS_LOKI_URL"
echo "  payload source   : $AADS_RELEASE_BASE_URL/aads-agent.tgz"
echo "  this node base   : $AADS_AGENT_BASE_URL"
echo "  environment      : $AADS_AGENT_ENVIRONMENT"
echo "  install alloy    : $AADS_INSTALL_ALLOY"
echo "  register node    : $([[ "$AADS_SKIP_REGISTER" == "true" ]] && echo no || echo yes)"

# ── System dependencies ─────────────────────────────────────────────────────
log "Installing system dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y python3 python3-venv uuid-runtime curl ca-certificates >/dev/null
command -v nginx >/dev/null 2>&1 || warn "nginx not installed — the agent installs fine, but nginx repair won't apply until nginx is present."
ok "Dependencies ready"

# ── Download + unpack the agent payload ─────────────────────────────────────
log "Downloading agent payload"
TMP_DIR="$(mktemp -d /tmp/aads-agent.XXXXXX)"
PAYLOAD="$TMP_DIR/aads-agent.tgz"
curl -fsSL "$AADS_RELEASE_BASE_URL/aads-agent.tgz" -o "$PAYLOAD" \
  || die "Failed to download $AADS_RELEASE_BASE_URL/aads-agent.tgz (is the server up?)."
tar -xzf "$PAYLOAD" -C "$TMP_DIR"
SRC="$TMP_DIR/pi-agent"
[[ -f "$SRC/install/install.sh" ]] || die "Payload missing pi-agent/install/install.sh."
ok "Payload unpacked"

# ── Install the agent (reuse the canonical installer) ───────────────────────
log "Installing On-Device Agent"
env \
  AADS_AGENT_TOKEN="$AADS_AGENT_TOKEN" \
  AADS_AGENT_ENVIRONMENT="$AADS_AGENT_ENVIRONMENT" \
  bash "$SRC/install/install.sh"
ok "Agent installed (systemd service: aads-agent)"

# ── Verify the service actually entered active (running) ────────────────────
log "Verifying service started"
SERVICE_UP=false
for _ in $(seq 1 15); do
  if systemctl is-active --quiet aads-agent 2>/dev/null; then
    SERVICE_UP=true; break
  fi
  sleep 1
done
if ! $SERVICE_UP; then
  warn "aads-agent.service failed to start. Journal output:"
  journalctl -u aads-agent -n 30 --no-pager 2>/dev/null || true
  die "Fix the service error above, then re-run this installer."
fi
ok "aads-agent.service is active"

# ── Wait for the agent HTTP API to become ready ─────────────────────────────
log "Waiting for agent to become ready"
FACTS=""
for _ in $(seq 1 30); do
  if FACTS="$(curl -fsS -H "Authorization: Bearer ${AADS_AGENT_TOKEN}" \
      "http://127.0.0.1:${AGENT_PORT}/v1/node/facts" 2>/dev/null)"; then
    break
  fi
  sleep 1
done
[[ -n "$FACTS" ]] || die "Agent did not respond on port ${AGENT_PORT}. Check: journalctl -u aads-agent -n 40"
NODE_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])' <<<"$FACTS")"
ok "Agent ready — node_id=$NODE_ID"

# ── Register the node with the server ───────────────────────────────────────
if [[ "$AADS_SKIP_REGISTER" != "true" ]]; then
  log "Registering node with server"
  PAYLOAD_JSON="$(python3 -c '
import json, sys
facts = json.loads(sys.argv[1])
print(json.dumps({
    "node_id":            facts["node_id"],
    "environment":        facts.get("environment", "test"),
    "agent_version":      facts.get("agent_version", "unknown"),
    "base_url":           sys.argv[2],
    "runner_capabilities": facts.get("runner_capabilities", {}),
}))' "$FACTS" "$AADS_AGENT_BASE_URL")"
  if curl -fsS \
      -H "X-Admin-API-Key: ${AADS_ADMIN_API_KEY}" \
      -H "Content-Type: application/json" \
      -d "$PAYLOAD_JSON" \
      "${AADS_DASHBOARD_URL}/api/agents/register" >/dev/null; then
    ok "Node registered at $AADS_DASHBOARD_URL"
  else
    warn "Registration call failed. Verify the admin key and that the dashboard is reachable at $AADS_DASHBOARD_URL."
    warn "You can re-register later with the same install command."
  fi
fi

# ── Install the Alloy log forwarder ─────────────────────────────────────────
if [[ "$AADS_INSTALL_ALLOY" == "true" ]]; then
  log "Installing Alloy log forwarder"
  if ! command -v alloy >/dev/null 2>&1; then
    apt-get install -y gpg wget >/dev/null
    install -d -m 0755 /etc/apt/keyrings
    wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
    chmod 0644 /etc/apt/keyrings/grafana.gpg
    echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
      > /etc/apt/sources.list.d/grafana.list
    apt-get update -qq
    apt-get install -y alloy >/dev/null
  fi
  install -d -m 0755 /etc/alloy
  sed -e "s|__NODE_ID__|${NODE_ID}|g" \
      -e "s|__LOKI_URL__|${AADS_LOKI_URL}|g" \
      "$SRC/alloy/config.alloy.tmpl" > /etc/alloy/config.alloy
  systemctl enable --now alloy
  systemctl is-active --quiet alloy && ok "Alloy forwarding logs to $AADS_LOKI_URL" \
    || warn "Alloy did not start cleanly. Check: journalctl -u alloy -n 40"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
HEALTH="$(curl -fsS -H "Authorization: Bearer ${AADS_AGENT_TOKEN}" \
  "http://127.0.0.1:${AGENT_PORT}/health" 2>/dev/null || echo '{}')"
log "Done"
echo "  node_id    : $NODE_ID"
echo "  base_url   : $AADS_AGENT_BASE_URL"
echo "  health     : $(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status","unknown"))' <<<"$HEALTH")"
echo
echo "  Verify from the server:"
echo "    curl -H \"Authorization: Bearer <token>\" $AADS_AGENT_BASE_URL/v1/node/facts"
echo "    curl -H \"X-Admin-API-Key: <admin-key>\" ${AADS_DASHBOARD_URL}/api/agents"
