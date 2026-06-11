#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

AGENT_TOKEN="${AADS_AGENT_TOKEN:-}"
AGENT_ENVIRONMENT="${AADS_AGENT_ENVIRONMENT:-test}"
AGENT_HOST="${AADS_AGENT_HOST:-0.0.0.0}"
AGENT_PORT="${AADS_AGENT_PORT:-8090}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$AGENT_TOKEN" ]]; then
  echo "AADS_AGENT_TOKEN is required" >&2
  exit 1
fi

id -u aads-agent >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/aads-agent --shell /usr/sbin/nologin aads-agent

install -d -o root -g root -m 0755 /etc/aads-agent
install -d -o root -g aads-agent -m 0750 /var/lib/aads-agent/snapshots/nginx
install -d -o root -g root -m 0755 /opt/aads-agent

if [[ ! -f /etc/aads-agent/node-id ]]; then
  uuidgen > /etc/aads-agent/node-id
fi
chown root:root /etc/aads-agent/node-id
chmod 0644 /etc/aads-agent/node-id

cat > /etc/aads-agent/agent.env <<EOF
AADS_AGENT_TOKEN=${AGENT_TOKEN}
AADS_AGENT_ENVIRONMENT=${AGENT_ENVIRONMENT}
AADS_AGENT_HOST=${AGENT_HOST}
AADS_AGENT_PORT=${AGENT_PORT}
AADS_NODE_ID_PATH=/etc/aads-agent/node-id
AADS_NGINX_SNAPSHOT_DIR=/var/lib/aads-agent/snapshots/nginx
EOF
chown root:root /etc/aads-agent/agent.env
chmod 0640 /etc/aads-agent/agent.env

python3 -m venv /opt/aads-agent/venv
/opt/aads-agent/venv/bin/pip install --upgrade pip
/opt/aads-agent/venv/bin/pip install -r "$SRC_DIR/requirements.txt"
install -o root -g root -m 0644 "$SRC_DIR/src/main.py" /opt/aads-agent/main.py

# Safety cards package (PolicyCard enforces the plan's ExecutionProfile).
install -d -o root -g root -m 0755 /opt/aads-agent/safety_cards
for card in "$SRC_DIR"/src/safety_cards/*.py; do
  install -o root -g root -m 0644 "$card" "/opt/aads-agent/safety_cards/$(basename "$card")"
done

# V2: install the single root runner wrapper plus every service wrapper. The
# wrappers are now invoked as plain argv (by the root runner or directly), not as
# catalog entries. sudoers authorizes ONLY the root runner.
install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-root-command-runner" /usr/local/sbin/aads-root-command-runner
for wrapper in "$SRC_DIR"/wrappers/aads-*; do
  name="$(basename "$wrapper")"
  [[ "$name" == "aads-root-command-runner" ]] && continue
  install -o root -g root -m 0755 "$wrapper" "/usr/local/sbin/$name"
done

# Security trade-off (deliberate, see docs/runner-v2-plan.md): this replaces the
# legacy per-wrapper exact-command grants with a single grant for the full-power
# root runner. OS-level argv constraint is gone; the safety boundary is now the
# agent's context-aware hook + append-only audit (and future safety cards).
# Intended for the test lab; production must add safety-card deny rules first.
cat > /etc/sudoers.d/aads-agent <<'EOF'
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-root-command-runner
EOF
chmod 0440 /etc/sudoers.d/aads-agent
visudo -cf /etc/sudoers.d/aads-agent

SNAPSHOT_CONF=/var/lib/aads-agent/snapshots/nginx/nginx.conf
REFRESH_SNAPSHOT="${AADS_REFRESH_NGINX_SNAPSHOT:-false}"
if [[ "$REFRESH_SNAPSHOT" == "true" || ! -f "$SNAPSHOT_CONF" ]]; then
  if nginx -t >/dev/null 2>&1; then
    if [[ -f /etc/nginx/nginx.conf ]]; then
      cp -a /etc/nginx/nginx.conf "$SNAPSHOT_CONF"
    fi
    if [[ -d /etc/nginx/sites-enabled ]]; then
      rm -rf /var/lib/aads-agent/snapshots/nginx/sites-enabled
      cp -a /etc/nginx/sites-enabled /var/lib/aads-agent/snapshots/nginx/sites-enabled
    fi
  else
    echo "warning: current nginx config is invalid; preserving existing known-good snapshot" >&2
  fi
fi
chown -R root:aads-agent /var/lib/aads-agent/snapshots/nginx
find /var/lib/aads-agent/snapshots/nginx -type d -exec chmod 0750 {} +
find /var/lib/aads-agent/snapshots/nginx -type f -exec chmod 0640 {} +

install -o root -g root -m 0644 "$SRC_DIR/systemd/aads-agent.service" /etc/systemd/system/aads-agent.service
systemctl daemon-reload
systemctl enable aads-agent
systemctl restart aads-agent
systemctl status --no-pager aads-agent
