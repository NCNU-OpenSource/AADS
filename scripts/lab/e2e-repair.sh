#!/usr/bin/env bash
# E2E nginx repair scenario runner.
# PVE environment: set AADS_CONTROLLER_IP and AADS_TARGET_IP.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER_IP="${AADS_CONTROLLER_IP:?AADS_CONTROLLER_IP must be set}"
TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"
TIMEOUT_SECONDS="${AADS_E2E_TIMEOUT_SECONDS:-260}"
SCENARIO="${1:-all}"

if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes"
else
  SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
fi

ctrl()   { ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" "$@"; }
target() { ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" "$@"; }

sql() {
  ctrl bash -lc \
    "cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb psql -U logdb -d logdb -At -c $(printf '%q' "$1")"
}

target_http_ok() {
  target bash -lc 'curl -fsS -o /dev/null http://127.0.0.1/'
}

target_nginx_ok() {
  target bash -lc 'sudo nginx -t >/dev/null && systemctl is-active --quiet nginx'
}

reset_target_baseline() {
  target sudo bash -lc '
    set -euo pipefail
    SNAPSHOT_DIR=/var/lib/aads-agent/snapshots/nginx
    test -f "$SNAPSHOT_DIR/nginx.conf"
    test -d "$SNAPSHOT_DIR/sites-enabled"
    cp -a "$SNAPSHOT_DIR/nginx.conf" /etc/nginx/nginx.conf
    rm -rf /etc/nginx/sites-enabled
    cp -a "$SNAPSHOT_DIR/sites-enabled" /etc/nginx/sites-enabled
    nginx -t >/dev/null
    systemctl restart nginx
    systemctl is-active --quiet nginx
    curl -fsS -o /dev/null http://127.0.0.1/
  '
}

wait_for_success() {
  local before_count="$1"
  local expected_command="$2"
  local deadline=$((SECONDS + TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    local latest
    latest="$(sql "SELECT execution_id || '|' || status FROM plan_executions ORDER BY requested_at DESC LIMIT 1;")"
    local count
    count="$(sql "SELECT count(*) FROM plan_executions;")"

    if [[ "$count" -gt "$before_count" && ( "$latest" == *"|kb_imported" || "$latest" == *"|kb_skipped" || "$latest" == *"|final_verified" ) ]]; then
      local execution_id="${latest%%|*}"
      local command_status
      # V2: the command_id column stores the service.operation label.
      command_status="$(sql "SELECT status FROM execution_steps WHERE execution_id = '$execution_id' AND command_id = '$expected_command' ORDER BY id DESC LIMIT 1;")"
      if [[ "$command_status" == "step_verified" ]]; then
        echo "Execution $execution_id succeeded with $expected_command"
        return 0
      fi
    fi
    sleep 5
  done

  echo "Timed out waiting for successful $expected_command execution" >&2
  sql "SELECT execution_id, plan_id, status, result FROM plan_executions ORDER BY requested_at DESC LIMIT 3;"
  return 1
}

run_scenario() {
  local name="$1"
  local breaker="$2"
  local expected_command="$3"
  local before_count

  echo "==> Baseline before $name"
  reset_target_baseline
  target_http_ok
  target_nginx_ok
  before_count="$(sql "SELECT count(*) FROM plan_executions;")"

  echo "==> Injecting $name"
  # Breaker scripts need TARGET_IP so they can SSH in to cause the fault.
  AADS_TARGET_IP="$TARGET_IP" AADS_SSH_USER="$SSH_USER" AADS_SSH_KEY="${SSH_KEY:-}" \
    "$ROOT/scripts/lab/$breaker"

  echo "==> Waiting for AADS repair using $expected_command"
  wait_for_success "$before_count" "$expected_command"

  echo "==> Verifying target recovered"
  target_http_ok
  target_nginx_ok
}

case "$SCENARIO" in
  stopped)
    run_scenario "nginx_stopped" "break-nginx-stopped.sh" "nginx.start"
    ;;
  bad-config)
    run_scenario "nginx_bad_config" "break-nginx-bad-config.sh" "nginx.restore_config"
    ;;
  all)
    run_scenario "nginx_stopped" "break-nginx-stopped.sh" "nginx.start"
    run_scenario "nginx_bad_config" "break-nginx-bad-config.sh" "nginx.restore_config"
    ;;
  *)
    echo "Usage: $0 [all|stopped|bad-config]" >&2
    exit 2
    ;;
esac

echo "E2E repair scenario(s) passed"
