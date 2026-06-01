#!/usr/bin/env bash
# ============================================================
# AADS Service-Coverage E2E Test Suite
#
# Tests AADS's ability to detect and repair failures in the
# services it monitors. Complements chaos-e2e.sh (which tests
# AADS infrastructure resilience) by verifying repair capability.
#
# Prerequisites on aads-target:
#   - postgresql installed and aads-agent wrappers deployed
#   - nginx installed (baseline)
#
# Usage:
#   ./scripts/lab/e2e-service-repair.sh              # all scenarios
#   ./scripts/lab/e2e-service-repair.sh SR-PG-01     # single scenario
#   ./scripts/lab/e2e-service-repair.sh --service pg  # all PostgreSQL
#
# Scenarios:
#   SR-PG-01  postgresql_stopped       (systemctl stop → AADS restarts)
#   SR-PG-02  postgresql_bad_config    (inject syntax error → restore)
#   SR-PG-03  postgresql_no_snapshot   (blocked: no known-good baseline)
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

# ── schedule ─────────────────────────────────────────────────

sr_schedule() {
  local filter="${1:-all}"
  local all_scenarios=(SR-PG-01 SR-PG-02 SR-PG-03)

  if [[ "$filter" =~ ^SR- ]]; then
    echo "$filter"; return
  fi
  if [[ "$filter" == "--service" ]]; then
    local svc="${2:-pg}"
    case "$svc" in
      pg|postgresql) printf 'SR-PG-01\nSR-PG-02\nSR-PG-03\n' ;;
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
