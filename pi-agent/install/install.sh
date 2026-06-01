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

install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-nginx-start" /usr/local/sbin/aads-nginx-start
install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-nginx-reload" /usr/local/sbin/aads-nginx-reload
install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-nginx-config-test" /usr/local/sbin/aads-nginx-config-test
install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-nginx-ensure-known-good-snapshot" /usr/local/sbin/aads-nginx-ensure-known-good-snapshot
install -o root -g root -m 0755 "$SRC_DIR/wrappers/aads-nginx-restore-known-good" /usr/local/sbin/aads-nginx-restore-known-good

cat > /etc/sudoers.d/aads-agent <<'EOF'
Defaults:aads-agent !requiretty
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-nginx-start
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-nginx-reload
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-nginx-config-test
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-nginx-ensure-known-good-snapshot
aads-agent ALL=(root) NOPASSWD: /usr/local/sbin/aads-nginx-restore-known-good
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
