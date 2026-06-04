#!/usr/bin/env bash
# Deploy the On-Device Agent (V2 runner) to a target VM via SSH.
# PVE environment: set AADS_TARGET_IP before running.
#
# Required env:
#   AADS_TARGET_IP    — IP of the target VM (e.g. 192.168.1.11)
# Optional env:
#   AADS_SSH_USER     — SSH user (default: ubuntu)
#   AADS_SSH_KEY      — path to private key; omit to use ssh-agent
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set (e.g. 192.168.1.11)}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"
TOKEN_FILE="$ROOT/.aads-lab-token"
ARCHIVE="$(mktemp /tmp/aads-target-deploy.XXXXXX.tgz)"

cleanup() { rm -f "$ARCHIVE"; }
trap cleanup EXIT

# Build SSH/SCP option strings.
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes"
else
  SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
fi

# Pack only the files the target needs.
COPYFILE_DISABLE=1 tar --exclude='._*' -C "$ROOT" -czf "$ARCHIVE" \
  pi-agent/src/main.py \
  pi-agent/systemd/aads-agent.service \
  pi-agent/wrappers

scp $SSH_OPTS "$ARCHIVE" "${SSH_USER}@${TARGET_IP}:/tmp/aads-target-deploy.tgz"
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" \
  'rm -rf /tmp/aads-target-deploy && mkdir -p /tmp/aads-target-deploy && \
   tar -xzf /tmp/aads-target-deploy.tgz -C /tmp/aads-target-deploy && \
   rm /tmp/aads-target-deploy.tgz'

ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" 'sudo bash -s' <<'REMOTE'
set -euo pipefail

SRC=/tmp/aads-target-deploy/pi-agent

if [[ ! -x /opt/aads-agent/venv/bin/uvicorn ]]; then
  echo "ERROR: /opt/aads-agent/venv is missing; bootstrap the target with:" >&2
  echo "  cd AADS && scripts/lab/up.sh  (or install the venv manually)" >&2
  exit 1
fi

install -d -o root -g root -m 0755 /opt/aads-agent /usr/local/sbin /etc/aads-agent
install -d -o root -g aads-agent -m 0750 \
  /var/lib/aads-agent/snapshots/nginx \
  /var/lib/aads-agent/snapshots/postgresql \
  /var/lib/aads-agent/snapshots/redis \
  /var/lib/aads-agent/snapshots/mysql

install -o root -g root -m 0644 "$SRC/src/main.py" /opt/aads-agent/main.py
install -o root -g root -m 0644 "$SRC/systemd/aads-agent.service" /etc/systemd/system/aads-agent.service

# Install root runner + all service wrappers (V2: argv targets, not catalog entries).
for wrapper in "$SRC"/wrappers/aads-*; do
  install -o root -g root -m 0755 "$wrapper" "/usr/local/sbin/$(basename "$wrapper")"
done

if ! grep -q "^AADS_ALLOWED_JOURNAL_UNITS=" /etc/aads-agent/agent.env 2>/dev/null; then
  echo "AADS_ALLOWED_JOURNAL_UNITS=nginx,aads-agent,postgresql,redis,redis-server,mysql,mariadb,docker" >> /etc/aads-agent/agent.env
fi
if ! grep -q "^AADS_DOCKER_ALLOWED_CONTAINERS=" /etc/aads-agent/agent.env 2>/dev/null; then
  echo "AADS_DOCKER_ALLOWED_CONTAINERS=aads-test-nginx" >> /etc/aads-agent/agent.env
fi
chown root:root /etc/aads-agent/agent.env
chmod 0640 /etc/aads-agent/agent.env

# V2: sudoers authorizes ONLY the single root runner.
# Service wrappers are invoked as plain argv by the root runner.
# See docs/runner-v2-plan.md §7 for the security trade-off.
cat > /etc/sudoers.d/aads-agent <<'EOF'
Defaults:aads-agent !requiretty
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-root-command-runner
EOF
chmod 0440 /etc/sudoers.d/aads-agent
visudo -cf /etc/sudoers.d/aads-agent

systemctl daemon-reload
systemctl restart aads-agent
systemctl is-active --quiet aads-agent
REMOTE

# Verify the agent came up and advertises runner capabilities.
if [[ -f "$TOKEN_FILE" ]]; then
  TOKEN="$(tr -d '\n' < "$TOKEN_FILE")"
  FACTS=""
  for _ in $(seq 1 30); do
    if FACTS="$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
        "http://${TARGET_IP}:8090/v1/node/facts" 2>/dev/null)"; then
      break
    fi
    sleep 1
  done
  if [[ -z "$FACTS" ]]; then
    echo "ERROR: target agent did not become ready on http://${TARGET_IP}:8090" >&2
    exit 1
  fi
  python3 -c '
import json, sys
d = json.load(sys.stdin)
caps = d.get("runner_capabilities", {})
print("Target agent ready: node={}, runner={} modes={} as_root={}".format(
  d.get("node_id", "unknown"),
  caps.get("schema_version", "?"),
  caps.get("modes", []),
  caps.get("supports_as_root")))
' <<<"$FACTS"
else
  echo "Target deployed. Skipping facts check because ${TOKEN_FILE} is missing."
fi

echo "Target agent deployed on ${TARGET_IP}"
