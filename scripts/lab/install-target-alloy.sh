#!/usr/bin/env bash
set -euo pipefail

LOKI_URL="${AADS_CONTROLLER_LOKI_URL:?AADS_CONTROLLER_LOKI_URL is required}"
NODE_ID="${AADS_TARGET_NODE_ID:?AADS_TARGET_NODE_ID is required}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root" >&2
  exit 1
fi

if ! command -v alloy >/dev/null 2>&1; then
  apt-get update
  apt-get install -y gpg wget ca-certificates
  install -d -m 0755 /etc/apt/keyrings
  wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor > /etc/apt/keyrings/grafana.gpg
  chmod 0644 /etc/apt/keyrings/grafana.gpg
  echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" > /etc/apt/sources.list.d/grafana.list
  apt-get update
  apt-get install -y alloy
fi

install -d -m 0755 /etc/alloy
cat > /etc/alloy/config.alloy <<EOF
logging {
  level  = "info"
  format = "logfmt"
}

loki.source.file "nginx_error" {
  targets = [
    {
      __path__         = "/var/log/nginx/error.log",
      job              = "nginx",
      source           = "target-nginx",
      node_id          = "${NODE_ID}",
      service          = "nginx",
      compose_service  = "nginx",
      container        = "nginx",
    },
  ]

  forward_to = [loki.write.controller.receiver]
}

loki.source.file "syslog" {
  targets = [
    {
      __path__ = "/var/log/syslog",
      job      = "syslog",
      source   = "target-syslog",
      node_id  = "${NODE_ID}",
      service  = "system",
    },
  ]

  forward_to = [loki.write.controller.receiver]
}

loki.write "controller" {
  endpoint {
    url = "${LOKI_URL}/loki/api/v1/push"
    headers = {
      "X-Scope-OrgID" = "raw",
    }
  }

  wal {
    enabled = true
  }
}
EOF

systemctl enable --now alloy
systemctl status --no-pager alloy
