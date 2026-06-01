#!/usr/bin/env bash
# ============================================================
# AADS Service-Coverage E2E Test Suite
#
# Tests AADS's ability to detect and repair failures in the
# services it monitors. Complements chaos-e2e.sh (which tests
# AADS infrastructure resilience) by verifying repair capability.
#
# Prerequisites on aads-target:
#   - wrappers deployed, sudoers updated
#   - services installed as needed (scenarios SKIP if service absent)
#
# Usage:
#   ./scripts/lab/e2e-service-repair.sh              # all scenarios
#   ./scripts/lab/e2e-service-repair.sh SR-PG-01     # single scenario
#   ./scripts/lab/e2e-service-repair.sh --service pg  # all PostgreSQL
#   ./scripts/lab/e2e-service-repair.sh --service redis
#   ./scripts/lab/e2e-service-repair.sh --service docker
#   ./scripts/lab/e2e-service-repair.sh --service mysql
#
# Scenarios:
#   SR-PG-01  postgresql_stopped       (systemctl stop → AADS restarts)
#   SR-PG-02  postgresql_bad_config    (inject syntax error → restore)
#   SR-PG-03  postgresql_no_snapshot   (blocked: no known-good baseline)
#   SR-RD-01  redis_stopped            (systemctl stop → AADS restarts)
#   SR-RD-02  redis_bad_config         (inject error → restore)
#   SR-DC-01  container_stopped        (docker stop → AADS restarts)
#   SR-DC-02  container_not_allowed    (policy.blocked: container_not_allowed)
#   SR-MY-01  mysql_stopped            (systemctl stop → AADS restarts)
#   SR-MY-02  mysql_bad_config         (inject error → restore)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER="${AADS_CONTROLLER_VM:-aads-controller}"
TARGET="${AADS_TARGET_VM:-aads-target}"
TIMEOUT_SECONDS="${AADS_SR_TIMEOUT:-300}"
ADMIN_KEY_FILE="${ROOT}/.aads-lab-admin-key"
DASH="http://192.168.252.2:5000"

PASS=0; FAIL=0; SKIP=0
RESULTS=()

# ── helpers (same as chaos-e2e.sh) ──────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "  ✅  $*"; }
fail() { echo "  ❌  $*" >&2; }
info() { echo "  ℹ️   $*"; }

if command -v gtimeout >/dev/null 2>&1; then
  timeout() { gtimeout "$@"; }
elif ! command -v timeout >/dev/null 2>&1; then
  timeout() {
    local secs=$1; shift
    perl -e 'alarm $ARGV[0]; exec @ARGV[1..$#ARGV] or die "exec: $!"' -- "$secs" "$@"
  }
fi

sql() {
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    "cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
     psql -U logdb -d logdb -At -c $(printf '%q' "$1")" 2>/dev/null
}

admin_key() { tr -d '\n' < "$ADMIN_KEY_FILE"; }

gate() {
  local method="$1" path="$2"; shift 2
  curl -s -m10 -X "$method" \
    -H "X-Admin-API-Key: $(admin_key)" \
    -H "Content-Type: application/json" \
    "$DASH$path" "$@"
}

record() {
  local name="$1" result="$2" note="$3"
  RESULTS+=("$name|$result|$note")
  if [[ "$result" == "PASS" ]]; then
    (( PASS++ )); ok "$name — PASS: $note"
  elif [[ "$result" == "SKIP" ]]; then
    (( SKIP++ )); info "$name — SKIP: $note"
  else
    (( FAIL++ )); fail "$name — FAIL: $note"
  fi
}

wait_new_plan() {
  local before="$1" deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local id
    id="$(sql "SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1;" || true)"
    [[ "$id" != "$before" && -n "$id" ]] && { echo "$id"; return 0; }
    sleep 8
  done
  return 1
}

wait_execution_terminal() {
  local exec_id="$1" deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local st; st="$(sql "SELECT status FROM plan_executions WHERE execution_id='$exec_id';" || true)"
    case "$st" in
      kb_skipped|kb_imported|final_verified|execution_failed|blocked|\
      execution_failed_unknown_state|failed_retryable)
        echo "$st"; return 0 ;;
    esac
    sleep 4
  done
  echo "timeout"; return 1
}

# ── target helpers ───────────────────────────────────────────

pg_installed() {
  multipass exec "$TARGET" -- bash -lc \
    'command -v pg_ctlcluster >/dev/null 2>&1 && systemctl list-units --type=service | grep -q postgresql' 2>/dev/null
}

pg_conf_dir() {
  multipass exec "$TARGET" -- bash -lc \
    'find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1' 2>/dev/null
}

reset_pg_baseline() {
  log "reset PostgreSQL baseline on $TARGET"
  # Restore config from snapshot if available, then restart
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/postgresql
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    if [[ -n "$CONF" && -f "$SNAP/postgresql.conf" ]]; then
      cp -a "$SNAP/postgresql.conf" "$CONF/"
      [[ -f "$SNAP/pg_hba.conf" ]] && cp -a "$SNAP/pg_hba.conf" "$CONF/"
    fi
    systemctl restart postgresql 2>/dev/null || true
    systemctl is-active postgresql >/dev/null 2>&1 && echo "pg_ok" || echo "pg_warn"
  ' 2>/dev/null || log "WARNING: reset_pg_baseline remote call failed (continuing)"
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_pg_snapshot() {
  # Create initial snapshot on target if missing (bootstrap step)
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/postgresql
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    if [[ -z "$CONF" ]]; then exit 1; fi
    mkdir -p "$SNAP"
    cp -a "$CONF/postgresql.conf" "$SNAP/"
    [[ -f "$CONF/pg_hba.conf" ]] && cp -a "$CONF/pg_hba.conf" "$SNAP/"
    chown -R root:aads-agent "$SNAP"
    chmod 0640 "$SNAP"/*.conf 2>/dev/null || true
    echo "snapshot_created=true"
  ' 2>/dev/null
}

# ── scenario implementations ─────────────────────────────────

# SR-PG-01: stop PostgreSQL → AADS detects → restarts → pg active
run_sr_pg_01() {
  log "SR-PG-01: postgresql_stopped"

  if ! pg_installed; then
    record SR-PG-01 SKIP "PostgreSQL not installed on $TARGET"; return
  fi

  ensure_pg_snapshot
  reset_pg_baseline
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-PG-01: stopping PostgreSQL"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc 'systemctl stop postgresql'

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-PG-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-01"}' -o /dev/null
  local ikey="sr-pg-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local pg_active; pg_active="$(multipass exec "$TARGET" -- bash -lc 'systemctl is-active postgresql' 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$pg_active" == "active" ]]; then
    record SR-PG-01 PASS "terminal=$terminal, postgresql=active ✓"
  else
    record SR-PG-01 FAIL "terminal=$terminal, postgresql=$pg_active"
  fi
}

# SR-PG-02: inject bad config → AADS detects → restore_known_good_config → pg active
run_sr_pg_02() {
  log "SR-PG-02: postgresql_bad_config"

  if ! pg_installed; then
    record SR-PG-02 SKIP "PostgreSQL not installed on $TARGET"; return
  fi

  ensure_pg_snapshot
  reset_pg_baseline
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-PG-02: injecting config syntax error"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    echo "invalid_directive_chaos = ???" >> "$CONF/postgresql.conf"
    pg_ver=$(basename "$(dirname "$CONF")")
    pg_cluster=$(basename "$CONF")
    pg_ctlcluster "$pg_ver" "$pg_cluster" reload 2>/dev/null || true
  '

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-PG-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-02"}' -o /dev/null
  local ikey="sr-pg-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local pg_active; pg_active="$(multipass exec "$TARGET" -- bash -lc 'systemctl is-active postgresql' 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$pg_active" == "active" ]]; then
    record SR-PG-02 PASS "terminal=$terminal, postgresql=active ✓"
  else
    record SR-PG-02 FAIL "terminal=$terminal, postgresql=$pg_active"
  fi
}

# SR-PG-03: delete snapshot, then stop pg → executor must block (no known-good baseline)
run_sr_pg_03() {
  log "SR-PG-03: postgresql_no_snapshot"

  if ! pg_installed; then
    record SR-PG-03 SKIP "PostgreSQL not installed on $TARGET"; return
  fi

  reset_pg_baseline
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-PG-03: removing PostgreSQL snapshot"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'rm -rf /var/lib/aads-agent/snapshots/postgresql'

  log "SR-PG-03: stopping PostgreSQL"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc 'systemctl stop postgresql'

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-PG-03 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-03"}' -o /dev/null
  local ikey="sr-pg-03-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  # Restore snapshot for subsequent tests
  ensure_pg_snapshot 2>/dev/null || true

  if [[ "$terminal" == "blocked" ]]; then
    local reason; reason="$(sql "SELECT result->>'reason' FROM plan_executions WHERE execution_id='$exec_id';" || true)"
    if [[ "$reason" == *snapshot* ]]; then
      record SR-PG-03 PASS "blocked:$reason ✓"
    else
      record SR-PG-03 FAIL "blocked but reason=$reason (want snapshot_failed)"
    fi
  else
    record SR-PG-03 FAIL "terminal=$terminal (want blocked)"
  fi
}

# ── Redis helpers ────────────────────────────────────────────

redis_installed() {
  multipass exec "$TARGET" -- bash -lc \
    'command -v redis-cli >/dev/null 2>&1 && (systemctl list-units --type=service | grep -qE "redis-server|redis\b")' 2>/dev/null
}

redis_service() {
  multipass exec "$TARGET" -- bash -lc \
    'systemctl cat redis-server >/dev/null 2>&1 && echo "redis-server" || echo "redis"' 2>/dev/null
}

reset_redis_baseline() {
  log "reset Redis baseline on $TARGET"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/redis
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    if [[ -f "$SNAP/redis.conf" ]]; then
      cp -a "$SNAP/redis.conf" "$CONF"
    fi
    systemctl restart redis-server 2>/dev/null || systemctl restart redis || true
    echo "redis_reset_done"
  ' 2>/dev/null || true
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_redis_snapshot() {
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/redis
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    if [[ ! -f "$CONF" ]]; then exit 1; fi
    mkdir -p "$SNAP"
    cp -a "$CONF" "$SNAP/redis.conf"
    chown -R root:aads-agent "$SNAP"
    chmod 0640 "$SNAP/redis.conf" 2>/dev/null || true
    echo "snapshot_created=true"
  ' 2>/dev/null
}

# SR-RD-01: stop Redis → AADS detects → restarts → active
run_sr_rd_01() {
  log "SR-RD-01: redis_stopped"
  if ! redis_installed; then
    record SR-RD-01 SKIP "Redis not installed on $TARGET"; return
  fi

  ensure_redis_snapshot
  reset_redis_baseline
  local svc; svc="$(redis_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-RD-01: stopping Redis ($svc)"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc "systemctl stop $svc"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-RD-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-rd-01"}' -o /dev/null
  local ikey="sr-rd-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local redis_active; redis_active="$(multipass exec "$TARGET" -- bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$redis_active" == "active" ]]; then
    record SR-RD-01 PASS "terminal=$terminal, redis=active ✓"
  else
    record SR-RD-01 FAIL "terminal=$terminal, redis=$redis_active"
  fi
}

# SR-RD-02: inject bad config → AADS detects → restore → active
run_sr_rd_02() {
  log "SR-RD-02: redis_bad_config"
  if ! redis_installed; then
    record SR-RD-02 SKIP "Redis not installed on $TARGET"; return
  fi

  ensure_redis_snapshot
  reset_redis_baseline
  local svc; svc="$(redis_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-RD-02: injecting Redis config error"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    echo "invalid_chaos_directive ???" >> "$CONF"
    systemctl restart redis-server 2>/dev/null || systemctl restart redis || true
  '

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-RD-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-rd-02"}' -o /dev/null
  local ikey="sr-rd-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local redis_active; redis_active="$(multipass exec "$TARGET" -- bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$redis_active" == "active" ]]; then
    record SR-RD-02 PASS "terminal=$terminal, redis=active ✓"
  else
    record SR-RD-02 FAIL "terminal=$terminal, redis=$redis_active"
  fi
}

# ── Docker helpers ────────────────────────────────────────────

docker_installed() {
  multipass exec "$TARGET" -- bash -lc 'command -v docker >/dev/null 2>&1' 2>/dev/null
}

TEST_CONTAINER="${AADS_TEST_CONTAINER:-aads-test-nginx}"

ensure_test_container() {
  timeout 60 multipass exec "$TARGET" -- bash -lc "
    docker ps -a --format '{{.Names}}' | grep -q '^${TEST_CONTAINER}\$' || \
      docker run -d --name ${TEST_CONTAINER} --restart unless-stopped nginx:alpine
    docker inspect --format '{{.State.Status}}' ${TEST_CONTAINER} | grep -q running || \
      docker start ${TEST_CONTAINER}
  " 2>/dev/null || true
}

# SR-DC-01: stop allowed container → AADS detects → restarts → running
run_sr_dc_01() {
  log "SR-DC-01: container_stopped"
  if ! docker_installed; then
    record SR-DC-01 SKIP "Docker not installed on $TARGET"; return
  fi

  ensure_test_container
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"
  sql "DELETE FROM node_locks;" 2>/dev/null || true

  log "SR-DC-01: stopping container $TEST_CONTAINER"
  timeout 30 multipass exec "$TARGET" -- bash -lc "docker stop $TEST_CONTAINER" 2>/dev/null

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-DC-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-dc-01"}' -o /dev/null
  local ikey="sr-dc-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local ctr_state; ctr_state="$(multipass exec "$TARGET" -- bash -lc "docker inspect --format '{{.State.Status}}' $TEST_CONTAINER" 2>/dev/null || echo 'unknown')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$ctr_state" == "running" ]]; then
    record SR-DC-01 PASS "terminal=$terminal, container=running ✓"
  else
    record SR-DC-01 FAIL "terminal=$terminal, container=$ctr_state"
  fi
}

# SR-DC-02: attempt restart of non-allowlisted container → policy.blocked
run_sr_dc_02() {
  log "SR-DC-02: container_not_allowed"
  if ! docker_installed; then
    record SR-DC-02 SKIP "Docker not installed on $TARGET"; return
  fi

  # Directly call the pi-agent API with a container not in allowlist
  local token; token="$(multipass exec "$TARGET" -- bash -lc 'cat /etc/aads-agent/agent-token 2>/dev/null || echo ""')"
  local agent_url="http://192.168.252.3:8090"
  local http_code
  http_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$agent_url/v1/actions/run" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{"command_id":"docker.container_restart","args":{"container":"not_allowed_container"}}' 2>/dev/null || echo '000')"

  if [[ "$http_code" == "400" ]]; then
    record SR-DC-02 PASS "pi-agent returned 400 for non-allowlisted container ✓"
  else
    record SR-DC-02 FAIL "expected 400, got HTTP $http_code"
  fi
}

# ── MySQL helpers ─────────────────────────────────────────────

mysql_installed() {
  multipass exec "$TARGET" -- bash -lc \
    '(command -v mysqld || command -v mariadbd) >/dev/null 2>&1 && \
     (systemctl list-units --type=service | grep -qE "mysql|mariadb")' 2>/dev/null
}

mysql_service() {
  multipass exec "$TARGET" -- bash -lc \
    'systemctl cat mysql >/dev/null 2>&1 && echo "mysql" || echo "mariadb"' 2>/dev/null
}

mysql_conf_file() {
  multipass exec "$TARGET" -- bash -lc \
    'find /etc/mysql -name "*.cnf" \( -path "*/mysql.conf.d/*" -o -path "*/mariadb.conf.d/*" -o -name "my.cnf" \) 2>/dev/null | sort | head -1' 2>/dev/null
}

reset_mysql_baseline() {
  log "reset MySQL baseline on $TARGET"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/mysql
    CONF=$(find /etc/mysql -name "*.cnf" \( -path "*/mysql.conf.d/*" -o -path "*/mariadb.conf.d/*" -o -name "my.cnf" \) 2>/dev/null | sort | head -1)
    if [[ -n "$CONF" && -f "$SNAP/my.cnf" ]]; then
      cp -a "$SNAP/my.cnf" "$CONF"
    fi
    SVC="mysql"; systemctl cat mysql >/dev/null 2>&1 || SVC="mariadb"
    systemctl restart "$SVC" 2>/dev/null || true
    echo "mysql_reset_done"
  ' 2>/dev/null || true
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_mysql_snapshot() {
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/mysql
    CONF=$(find /etc/mysql -name "*.cnf" \( -path "*/mysql.conf.d/*" -o -path "*/mariadb.conf.d/*" -o -name "my.cnf" \) 2>/dev/null | sort | head -1)
    if [[ -z "$CONF" ]]; then exit 1; fi
    mkdir -p "$SNAP"
    cp -a "$CONF" "$SNAP/my.cnf"
    chown -R root:aads-agent "$SNAP"
    chmod 0640 "$SNAP/my.cnf" 2>/dev/null || true
    echo "snapshot_created=true"
  ' 2>/dev/null
}

# SR-MY-01: stop MySQL → AADS detects → restarts → active
run_sr_my_01() {
  log "SR-MY-01: mysql_stopped"
  if ! mysql_installed; then
    record SR-MY-01 SKIP "MySQL/MariaDB not installed on $TARGET"; return
  fi

  ensure_mysql_snapshot
  reset_mysql_baseline
  local svc; svc="$(mysql_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-MY-01: stopping $svc"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc "systemctl stop $svc"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-MY-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-my-01"}' -o /dev/null
  local ikey="sr-my-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local svc_active; svc_active="$(multipass exec "$TARGET" -- bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$svc_active" == "active" ]]; then
    record SR-MY-01 PASS "terminal=$terminal, $svc=active ✓"
  else
    record SR-MY-01 FAIL "terminal=$terminal, $svc=$svc_active"
  fi
}

# SR-MY-02: inject bad config → AADS detects → restore → active
run_sr_my_02() {
  log "SR-MY-02: mysql_bad_config"
  if ! mysql_installed; then
    record SR-MY-02 SKIP "MySQL/MariaDB not installed on $TARGET"; return
  fi

  ensure_mysql_snapshot
  reset_mysql_baseline
  local svc; svc="$(mysql_service)"
  local conf; conf="$(mysql_conf_file)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-MY-02: injecting MySQL config error into $conf"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc "
    echo '[chaos_section_invalid]' >> '$conf'
    echo 'chaos_option = ???' >> '$conf'
    SVC=$svc; systemctl restart \"\$SVC\" 2>/dev/null || true
  "

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record SR-MY-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-my-02"}' -o /dev/null
  local ikey="sr-my-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local svc_active; svc_active="$(multipass exec "$TARGET" -- bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$svc_active" == "active" ]]; then
    record SR-MY-02 PASS "terminal=$terminal, $svc=active ✓"
  else
    record SR-MY-02 FAIL "terminal=$terminal, $svc=$svc_active"
  fi
}

# ── schedule ─────────────────────────────────────────────────

sr_schedule() {
  local filter="${1:-all}"
  local all_scenarios=(SR-PG-01 SR-PG-02 SR-PG-03 SR-RD-01 SR-RD-02 SR-DC-01 SR-DC-02 SR-MY-01 SR-MY-02)

  if [[ "$filter" =~ ^SR- ]]; then
    echo "$filter"; return
  fi
  if [[ "$filter" == "--service" ]]; then
    local svc="${2:-pg}"
    case "$svc" in
      pg|postgresql) printf 'SR-PG-01\nSR-PG-02\nSR-PG-03\n' ;;
      redis|rd)      printf 'SR-RD-01\nSR-RD-02\n' ;;
      docker|dc)     printf 'SR-DC-01\nSR-DC-02\n' ;;
      mysql|my)      printf 'SR-MY-01\nSR-MY-02\n' ;;
      *) echo "unknown service: $svc" >&2; exit 2 ;;
    esac
    return
  fi
  printf '%s\n' "${all_scenarios[@]}"
}

# ── main ─────────────────────────────────────────────────────

if ! bash "$ROOT/scripts/lab/check-prereqs.sh" >/dev/null 2>&1; then
  echo "ERROR: prerequisites check failed — run scripts/lab/check-prereqs.sh for details" >&2
  exit 1
fi

if [[ ! -f "$ADMIN_KEY_FILE" ]]; then
  echo "ERROR: admin key not found at $ADMIN_KEY_FILE" >&2
  echo "       Create it with: multipass exec $CONTROLLER -- cat ~/.aads-lab-admin-key > $ADMIN_KEY_FILE" >&2
  exit 1
fi

SCENARIO="${1:-all}"
log "AADS Service-Coverage E2E — scenario=$SCENARIO"
log "Controller: $CONTROLLER | Target: $TARGET | Timeout: ${TIMEOUT_SECONDS}s"

RUN_LIST=()
while IFS= read -r line; do RUN_LIST+=("$line"); done < <(sr_schedule "$@")
log "Run list: ${RUN_LIST[*]}"

for scenario in "${RUN_LIST[@]}"; do
  echo
  log "━━━ $scenario ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  case "$scenario" in
    SR-PG-01) run_sr_pg_01 ;;
    SR-PG-02) run_sr_pg_02 ;;
    SR-PG-03) run_sr_pg_03 ;;
    SR-RD-01) run_sr_rd_01 ;;
    SR-RD-02) run_sr_rd_02 ;;
    SR-DC-01) run_sr_dc_01 ;;
    SR-DC-02) run_sr_dc_02 ;;
    SR-MY-01) run_sr_my_01 ;;
    SR-MY-02) run_sr_my_02 ;;
    *)        record "$scenario" SKIP "unknown scenario" ;;
  esac
done

# ── report ────────────────────────────────────────────────────
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AADS Service-Coverage E2E Results"
printf "  %-32s  %-10s  %s\n" "Scenario" "Result" "Notes"
echo "  ────────────────────────────────────────────────────────"
for r in "${RESULTS[@]}"; do
  IFS='|' read -r name result note <<< "$r"
  printf "  %-32s  %-10s  %s\n" "$name" "$result" "$note"
done
echo "  ────────────────────────────────────────────────────────"
echo "  PASS: $PASS   FAIL: $FAIL   SKIP: $SKIP"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
[[ $FAIL -eq 0 ]]
