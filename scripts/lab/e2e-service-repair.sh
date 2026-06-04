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
CONTROLLER_IP="${AADS_CONTROLLER_IP:?AADS_CONTROLLER_IP must be set}"
TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"

if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS="-i ${SSH_KEY} -o StrictHostKeyChecking=no -o BatchMode=yes"
else
  SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
fi

ctrl()   { ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" "$@"; }
target() { ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" "$@"; }
TIMEOUT_SECONDS="${AADS_SR_TIMEOUT:-300}"
ADMIN_KEY_FILE="${ROOT}/.aads-lab-admin-key"
DASH="http://${CONTROLLER_IP}:5000"

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
  timeout 30 ctrl bash -lc \
    "cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb psql -U logdb -d logdb -At -c $(printf '%q' "$1")" 2>/dev/null
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
  local before="$1" expected_command_prefix="${2:-}" deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local id
    if [[ -n "$expected_command_prefix" ]]; then
      id="$(sql "SELECT diagnosis_id FROM diagnosis_reports WHERE timestamp > COALESCE((SELECT timestamp FROM diagnosis_reports WHERE diagnosis_id = '$before'), '-infinity'::timestamptz) AND action_plan::text ILIKE '%$expected_command_prefix%' ORDER BY timestamp DESC LIMIT 1;" || true)"
    else
      id="$(sql "SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1;" || true)"
    fi
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
  target bash -lc \
    'command -v pg_ctlcluster >/dev/null 2>&1 && systemctl cat postgresql >/dev/null 2>&1' 2>/dev/null
}

pg_conf_dir() {
  target bash -lc \
    'find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1' 2>/dev/null
}

reset_pg_baseline() {
  log "reset PostgreSQL baseline on $TARGET"
  # Restore config from snapshot if available, then restart
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/postgresql
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    if [[ -n "$CONF" && -f "$SNAP/postgresql.conf" ]]; then
      cp "$SNAP/postgresql.conf" "$CONF/postgresql.conf"
      chown postgres:postgres "$CONF/postgresql.conf"
      chmod 0644 "$CONF/postgresql.conf"
      if [[ -f "$SNAP/pg_hba.conf" ]]; then
        cp "$SNAP/pg_hba.conf" "$CONF/pg_hba.conf"
        chown postgres:postgres "$CONF/pg_hba.conf"
        chmod 0640 "$CONF/pg_hba.conf"
      fi
    fi
    systemctl restart postgresql 2>/dev/null || true
    pg_isready -q && echo "pg_ok" || echo "pg_warn"
  ' 2>/dev/null || log "WARNING: reset_pg_baseline remote call failed (continuing)"
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_pg_snapshot() {
  # Create initial snapshot on target if missing (bootstrap step)
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/postgresql
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    if [[ -z "$CONF" ]]; then exit 1; fi
    mkdir -p "$SNAP"
    cp "$CONF/postgresql.conf" "$SNAP/postgresql.conf"
    [[ -f "$CONF/pg_hba.conf" ]] && cp "$CONF/pg_hba.conf" "$SNAP/pg_hba.conf"
    chown -R root:aads-agent "$SNAP"
    chmod 0640 "$SNAP"/*.conf 2>/dev/null || true
    echo "snapshot_created=true"
  ' 2>/dev/null
}

restore_pg_after_snapshot_guard() {
  ensure_pg_snapshot 2>/dev/null || true
  reset_pg_baseline
}

# ── scenario implementations ─────────────────────────────────

# SR-PG-01: stop PostgreSQL → AADS detects → restarts → pg active
run_sr_pg_01() {
  log "SR-PG-01: postgresql_stopped"

  if ! pg_installed; then
    record SR-PG-01 SKIP "PostgreSQL not installed on $TARGET"; return
  fi

  reset_pg_baseline
  ensure_pg_snapshot
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-PG-01: stopping PostgreSQL"
  timeout 30 target sudo bash -lc '
    systemctl stop postgresql
    MARKER="sr-pg-01-$(date +%s)"
    logger -t postgresql "postgresql failed to start: service stopped by AADS lab $MARKER"
    LOG=$(find /var/log/postgresql -name "postgresql-*.log" 2>/dev/null | sort | head -1)
    if [[ -n "$LOG" ]]; then
      { printf "%s [error] postgresql failed to start: service stopped by AADS lab %s\n" "$(date -Is)" "$MARKER" >> "$LOG"; } 2>/dev/null || true
    fi
  '

  local plan_id; plan_id="$(wait_new_plan "$before" "postgresql.")" || { record SR-PG-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-01"}' -o /dev/null
  local ikey="sr-pg-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local pg_active; pg_active="$(target bash -lc 'systemctl is-active postgresql' 2>/dev/null || echo 'inactive')"

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

  reset_pg_baseline
  ensure_pg_snapshot
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-PG-02: injecting config syntax error"
  timeout 30 target sudo bash -lc '
    MARKER="sr-pg-02-$(date +%s)"
    CONF=$(find /etc/postgresql -name postgresql.conf -exec dirname {} \; 2>/dev/null | sort | head -1)
    echo "invalid_directive_chaos_$MARKER = ???" >> "$CONF/postgresql.conf"
    systemctl restart postgresql 2>/dev/null || true
    logger -t postgresql "postgresql config error: invalid directive from AADS lab $MARKER"
  '

  local plan_id; plan_id="$(wait_new_plan "$before" "postgresql.")" || { record SR-PG-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-02"}' -o /dev/null
  local ikey="sr-pg-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local pg_active; pg_active="$(target bash -lc 'systemctl is-active postgresql' 2>/dev/null || echo 'inactive')"

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
  timeout 30 target sudo bash -lc \
    'rm -rf /var/lib/aads-agent/snapshots/postgresql'

  log "SR-PG-03: stopping PostgreSQL"
  timeout 30 target sudo bash -lc '
    systemctl stop postgresql
    MARKER="sr-pg-03-$(date +%s)"
    logger -t postgresql "postgresql failed to start: missing snapshot guard AADS lab $MARKER"
  '

  local plan_id; plan_id="$(wait_new_plan "$before" "postgresql.")" || { restore_pg_after_snapshot_guard; record SR-PG-03 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-pg-03"}' -o /dev/null
  local ikey="sr-pg-03-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  # Restore snapshot and service health for subsequent tests/final target state.
  restore_pg_after_snapshot_guard

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
  target bash -lc \
    'command -v redis-cli >/dev/null 2>&1 && (systemctl cat redis-server >/dev/null 2>&1 || systemctl cat redis >/dev/null 2>&1)' 2>/dev/null
}

redis_service() {
  target bash -lc \
    'systemctl cat redis-server >/dev/null 2>&1 && echo "redis-server" || echo "redis"' 2>/dev/null
}

reset_redis_baseline() {
  log "reset Redis baseline on $TARGET"
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/redis
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    if [[ -f "$SNAP/redis.conf" ]]; then
      cp "$SNAP/redis.conf" "$CONF"
      chown redis:redis "$CONF" 2>/dev/null || chown root:redis "$CONF" 2>/dev/null || true
      chmod 0640 "$CONF"
    fi
    systemctl restart redis-server 2>/dev/null || systemctl restart redis || true
    [[ "$(redis-cli ping 2>/dev/null || true)" == "PONG" ]] && echo "redis_ok" || echo "redis_warn"
  ' 2>/dev/null || true
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_redis_snapshot() {
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/redis
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    if [[ ! -f "$CONF" ]]; then exit 1; fi
    redis-server "$CONF" --test-config >/dev/null 2>&1
    mkdir -p "$SNAP"
    cp "$CONF" "$SNAP/redis.conf"
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

  reset_redis_baseline
  ensure_redis_snapshot
  local svc; svc="$(redis_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-RD-01: stopping Redis ($svc)"
  timeout 30 target sudo bash -lc "
    systemctl stop $svc
    MARKER=sr-rd-01-\$(date +%s)
    logger -t $svc \"redis failed to start: service stopped by AADS lab \$MARKER\"
    LOG=/var/log/redis/redis-server.log
    if [[ -f \"\$LOG\" ]]; then
      { printf '%s [error] redis failed to start: service stopped by AADS lab %s\n' \"\$(date -Is)\" \"\$MARKER\" >> \"\$LOG\"; } 2>/dev/null || true
    fi
  "

  local plan_id; plan_id="$(wait_new_plan "$before" "redis.")" || { record SR-RD-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-rd-01"}' -o /dev/null
  local ikey="sr-rd-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local redis_active; redis_active="$(target bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

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

  reset_redis_baseline
  ensure_redis_snapshot
  local svc; svc="$(redis_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-RD-02: injecting Redis config error"
  timeout 30 target sudo bash -lc '
    MARKER="sr-rd-02-$(date +%s)"
    CONF="${AADS_REDIS_CONF:-/etc/redis/redis.conf}"
    echo "invalid_chaos_directive_$MARKER ???" >> "$CONF"
    systemctl restart redis-server 2>/dev/null || systemctl restart redis || true
    logger -t redis-server "redis config error: invalid directive from AADS lab $MARKER"
  '

  local plan_id; plan_id="$(wait_new_plan "$before" "redis.")" || { record SR-RD-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-rd-02"}' -o /dev/null
  local ikey="sr-rd-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local redis_active; redis_active="$(target bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$redis_active" == "active" ]]; then
    record SR-RD-02 PASS "terminal=$terminal, redis=active ✓"
  else
    record SR-RD-02 FAIL "terminal=$terminal, redis=$redis_active"
  fi
}

# ── Docker helpers ────────────────────────────────────────────

docker_installed() {
  target bash -lc 'command -v docker >/dev/null 2>&1' 2>/dev/null
}

TEST_CONTAINER="${AADS_TEST_CONTAINER:-aads-test-nginx}"

ensure_test_container() {
  timeout 60 target bash -lc "
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
  timeout 30 target bash -lc "
    docker stop $TEST_CONTAINER
    MARKER=sr-dc-01-\$(date +%s)
    logger -t docker 'docker container $TEST_CONTAINER exited unexpectedly: AADS lab' \"\$MARKER\"
  " 2>/dev/null

  local plan_id; plan_id="$(wait_new_plan "$before" "docker.")" || { record SR-DC-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-dc-01"}' -o /dev/null
  local ikey="sr-dc-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local ctr_state; ctr_state="$(target bash -lc "docker inspect --format '{{.State.Status}}' $TEST_CONTAINER" 2>/dev/null || echo 'unknown')"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]] \
     && [[ "$ctr_state" == "running" ]]; then
    record SR-DC-01 PASS "terminal=$terminal, container=running ✓"
  else
    record SR-DC-01 FAIL "terminal=$terminal, container=$ctr_state"
  fi
}

# SR-DC-02: attempt restart of non-allowlisted container → policy.blocked
run_sr_dc_02() {
  log "SR-DC-02: legacy catalog endpoint removed (V2)"

  # V2: the catalog endpoints (/v1/actions/run, /v1/probes/run) are gone; the
  # only executor path is /v1/commands/run. Assert the legacy endpoint 404/405s,
  # proving the catalog command whitelist was removed.
  local token; token="$(target bash -lc 'cat /etc/aads-agent/agent-token 2>/dev/null || echo ""')"
  local agent_url="http://${TARGET_IP}:8090"
  local http_code
  http_code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "$agent_url/v1/actions/run" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -d '{"command_id":"docker.container_restart","args":{"container":"x"}}' 2>/dev/null || echo '000')"

  if [[ "$http_code" == "404" || "$http_code" == "405" ]]; then
    record SR-DC-02 PASS "legacy /v1/actions/run removed (HTTP $http_code) ✓"
  else
    record SR-DC-02 FAIL "expected 404/405 for removed catalog endpoint, got HTTP $http_code"
  fi
}

# ── MySQL helpers ─────────────────────────────────────────────

mysql_installed() {
  target bash -lc \
    '(command -v mysqld || command -v mariadbd) >/dev/null 2>&1 && \
     (systemctl cat mysql >/dev/null 2>&1 || systemctl cat mariadb >/dev/null 2>&1)' 2>/dev/null
}

mysql_service() {
  target bash -lc \
    'systemctl cat mysql >/dev/null 2>&1 && echo "mysql" || echo "mariadb"' 2>/dev/null
}

mysql_conf_file() {
  target bash -lc \
    'for candidate in "${AADS_MYSQL_CONF:-}" /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mariadb.conf.d/50-server.cnf /etc/mysql/my.cnf; do
       [[ -n "$candidate" && -f "$candidate" ]] && { printf "%s\n" "$candidate"; exit 0; }
     done
     find /etc/mysql -type f -name "*.cnf" \( -name "mysqld.cnf" -o -name "*server*.cnf" -o -path "*/mariadb.conf.d/*.cnf" \) 2>/dev/null | sort | head -1' 2>/dev/null
}

reset_mysql_baseline() {
  log "reset MySQL baseline on $TARGET"
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/mysql
    CONF=""
    for candidate in "${AADS_MYSQL_CONF:-}" /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mariadb.conf.d/50-server.cnf /etc/mysql/my.cnf; do
      [[ -n "$candidate" && -f "$candidate" ]] && { CONF="$candidate"; break; }
    done
    if [[ -z "$CONF" ]]; then
      CONF=$(find /etc/mysql -type f -name "*.cnf" \( -name "mysqld.cnf" -o -name "*server*.cnf" -o -path "*/mariadb.conf.d/*.cnf" \) 2>/dev/null | sort | head -1)
    fi
    if [[ -n "$CONF" && -f "$SNAP/my.cnf" ]]; then
      cp "$SNAP/my.cnf" "$CONF"
      chown root:root "$CONF"
      chmod 0644 "$CONF"
    fi
    SVC="mysql"; systemctl cat mysql >/dev/null 2>&1 || SVC="mariadb"
    systemctl restart "$SVC" 2>/dev/null || true
    mysqladmin -u root --connect-timeout=5 ping >/dev/null 2>&1 && echo "mysql_ok" || echo "mysql_warn"
  ' 2>/dev/null || true
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

ensure_mysql_snapshot() {
  timeout 30 target sudo bash -lc '
    SNAP=/var/lib/aads-agent/snapshots/mysql
    CONF=""
    for candidate in "${AADS_MYSQL_CONF:-}" /etc/mysql/mysql.conf.d/mysqld.cnf /etc/mysql/mariadb.conf.d/50-server.cnf /etc/mysql/my.cnf; do
      [[ -n "$candidate" && -f "$candidate" ]] && { CONF="$candidate"; break; }
    done
    if [[ -z "$CONF" ]]; then
      CONF=$(find /etc/mysql -type f -name "*.cnf" \( -name "mysqld.cnf" -o -name "*server*.cnf" -o -path "*/mariadb.conf.d/*.cnf" \) 2>/dev/null | sort | head -1)
    fi
    if [[ -z "$CONF" ]]; then exit 1; fi
    SVC="mysql"; systemctl cat mysql >/dev/null 2>&1 || SVC="mariadb"
    systemctl is-active --quiet "$SVC"
    mysqladmin -u root --connect-timeout=5 ping >/dev/null 2>&1
    mysqld --validate-config >/dev/null 2>&1 || mariadbd --validate-config >/dev/null 2>&1
    mkdir -p "$SNAP"
    cp "$CONF" "$SNAP/my.cnf"
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

  reset_mysql_baseline
  ensure_mysql_snapshot
  local svc; svc="$(mysql_service)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-MY-01: stopping $svc"
  timeout 30 target sudo bash -lc "
    systemctl stop $svc
    MARKER=sr-my-01-\$(date +%s)
    logger -t $svc \"mysql failed to start: service stopped by AADS lab \$MARKER\"
    LOG=/var/log/mysql/error.log
    if [[ -f \"\$LOG\" ]]; then
      { printf '%s [ERROR] mysql failed to start: service stopped by AADS lab %s\n' \"\$(date -Is)\" \"\$MARKER\" >> \"\$LOG\"; } 2>/dev/null || true
    fi
  "

  local plan_id; plan_id="$(wait_new_plan "$before" "mysql.")" || { record SR-MY-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-my-01"}' -o /dev/null
  local ikey="sr-my-01-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local svc_active; svc_active="$(target bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

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

  reset_mysql_baseline
  ensure_mysql_snapshot
  local svc; svc="$(mysql_service)"
  local conf; conf="$(mysql_conf_file)"
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  log "SR-MY-02: injecting MySQL config error into $conf"
  timeout 30 target sudo bash -lc "
    MARKER=sr-my-02-\$(date +%s)
    echo \"[chaos_section_invalid_\$MARKER]\" >> '$conf'
    echo 'chaos_option = ???' >> '$conf'
    SVC=$svc; systemctl restart \"\$SVC\" 2>/dev/null || true
    logger -t $svc \"mysql config error: invalid directive from AADS lab \$MARKER\"
  "

  local plan_id; plan_id="$(wait_new_plan "$before" "mysql.")" || { record SR-MY-02 FAIL "no plan generated"; return; }
  info "plan: $plan_id"

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"sr-my-02"}' -o /dev/null
  local ikey="sr-my-02-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  local svc_active; svc_active="$(target bash -lc "systemctl is-active $svc" 2>/dev/null || echo 'inactive')"

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
  echo "       Create it with: ssh ${SSH_USER}@${CONTROLLER_IP} cat ~/.aads-lab-admin-key > $ADMIN_KEY_FILE" >&2
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
