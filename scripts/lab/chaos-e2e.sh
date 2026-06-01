#!/usr/bin/env bash
# ============================================================
# AADS Chaos E2E Test Suite
#
# Inspired by Netflix Chaos Monkey: actively inject failures at
# every layer of the AADS pipeline to verify:
#   - state machine consistency (audit_events, plan_status)
#   - node lock lifecycle (no leaked locks on failure)
#   - graceful degradation (no crash when upstream is gone)
#   - recovery correctness (executor resumes from DB state)
#
# Usage:
#   ./scripts/lab/chaos-e2e.sh              # all scenarios
#   ./scripts/lab/chaos-e2e.sh CM-01        # single scenario
#   ./scripts/lab/chaos-e2e.sh --random 3   # pick 3 at random
#   ./scripts/lab/chaos-e2e.sh --seed 42 --random 5
#
# Scenarios:
#   CM-01  agent_killed_mid_execution     (post-snapshot, pre-step)
#   CM-02  executor_crashed_mid_step      (kill layer4-executor container)
#   CM-03  loki_unavailable               (layer1 graceful degradation)
#   CM-04  litellm_down                   (layer2 graceful degradation)
#   CM-05  approval_expired_before_exec   (gate expiry race)
#   CM-06  concurrent_repair_lock         (two plans, same target)
#   CM-07  snapshot_missing               (blocked:no_snapshot)
#   CM-08  network_partition              (iptables block :8090)
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER="${AADS_CONTROLLER_VM:-aads-controller}"
TARGET="${AADS_TARGET_VM:-aads-target}"
TIMEOUT_SECONDS="${AADS_CHAOS_TIMEOUT:-200}"
ADMIN_KEY_FILE="${ROOT}/.aads-lab-admin-key"
DASH="http://192.168.252.2:5000"

PASS=0; FAIL=0; SKIP=0
RESULTS=()

# ── helpers ─────────────────────────────────────────────────

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "  ✅  $*"; }
fail() { echo "  ❌  $*" >&2; }
info() { echo "  ℹ️   $*"; }

# macOS compat: GNU 'timeout' is not built-in.
# If gtimeout (coreutils) is available use it; otherwise shim with background+kill.
if command -v gtimeout >/dev/null 2>&1; then
  timeout() { gtimeout "$@"; }
elif ! command -v timeout >/dev/null 2>&1; then
  # macOS: GNU timeout not available. Use perl alarm() — always present on macOS.
  # perl -e 'alarm N; exec @ARGV' sets an alarm, then exec's the command directly
  # in the same process, so no orphan subprocesses.
  timeout() {
    local secs=$1; shift
    perl -e 'alarm $ARGV[0]; exec @ARGV[1..$#ARGV] or die "exec: $!"' -- "$secs" "$@"
  }
fi

sql() {
  # No timeout wrapper here: psql queries are fast and perl alarm() propagates
  # into exec'd children, causing SIGALRM on the parent shell when called many
  # times in a poll loop with set -euo pipefail. Callers add "|| true" where needed.
  multipass exec "$CONTROLLER" -- bash -lc \
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

target_nginx_ok() {
  timeout 30 multipass exec "$TARGET" -- bash -lc \
    'sudo nginx -t >/dev/null 2>&1 && systemctl is-active --quiet nginx \
     && curl -fsS --max-time 8 -o /dev/null http://127.0.0.1/'
}

reset_baseline() {
  log "reset baseline on $TARGET"
  # Use 'bash +e' inside the remote shell so individual failures don't abort
  # the reset. We explicitly check the outcome with final nginx/curl tests.
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    set +e  # tolerate partial failures in the restore sequence
    SNAP=/var/lib/aads-agent/snapshots/nginx

    # Fix snapshot: remove any nested sites-enabled left by a prior cp-into-existing-dir
    rm -rf "$SNAP/sites-enabled/sites-enabled" 2>/dev/null

    # Restore nginx.conf
    cp -a "$SNAP/nginx.conf" /etc/nginx/nginx.conf

    # Restore sites-enabled atomically
    rm -rf /etc/nginx/sites-enabled
    cp -a "$SNAP/sites-enabled/." /tmp/ses 2>/dev/null && mv /tmp/ses /etc/nginx/sites-enabled

    nginx -t >/dev/null 2>&1 && systemctl restart nginx || systemctl start nginx
    # Report outcome (exit 0 always — let the outer check decide)
    systemctl is-active nginx >/dev/null 2>&1 && \
      curl -fsS --max-time 8 -o /dev/null http://127.0.0.1/ && echo "nginx_ok" || echo "nginx_warn"
  ' 2>/dev/null || log "WARNING: reset_baseline remote call failed (continuing)"

  # Restart Knowledge Agent in case a prior test left it stopped
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab start layer4-executor 2>/dev/null' 2>/dev/null || true
  # Release any stale node locks (failure here is non-fatal)
  sql "DELETE FROM node_locks;" 2>/dev/null || true
}

break_nginx_stopped() {
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'systemctl stop nginx; logger -t nginx "nginx failed to start: chaos-e2e stop"
     printf "%s [error] nginx failed to start: chaos-e2e stop\n" "$(date -Is)" >> /var/log/nginx/error.log'
}

break_nginx_bad_config() {
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.chaos-bak
     echo "chaos {" >> /etc/nginx/nginx.conf
     systemctl reload nginx || true
     printf "%s [emerg] chaos: [emerg] invalid chaos directive\n" "$(date -Is)" >> /var/log/nginx/error.log'
}

wait_new_plan() {
  # Wait until a new diagnosis_id appears and return it.
  # sql calls use '|| true' so a transient multipass/psql error doesn't abort
  # the script under set -euo pipefail.
  local before="$1" deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local id
    id="$(sql "SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1;" || true)"
    [[ "$id" != "$before" && -n "$id" ]] && { echo "$id"; return 0; }
    sleep 8
  done
  return 1
}

wait_plan_status() {
  local plan_id="$1" expected_status="$2" deadline=$((SECONDS + TIMEOUT_SECONDS))
  while (( SECONDS < deadline )); do
    local st; st="$(sql "SELECT plan_status FROM diagnosis_reports WHERE diagnosis_id='$plan_id';" || true)"
    [[ "$st" == "$expected_status" ]] && return 0
    sleep 4
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

approve_and_execute() {
  local plan_id="$1"
  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-e2e"}' -o /dev/null
  local ikey="chaos-$(date +%s)-$RANDOM"
  gate POST "/api/plans/$plan_id/execute" \
    -H "Idempotency-Key: $ikey" \
    -o /dev/null -w "%{http_code}"
}

record() {
  local name="$1" result="$2" note="$3"
  RESULTS+=("$name|$result|$note")
  if [[ "$result" == "PASS" ]]; then
    (( PASS++ )); ok "$name — PASS"
  elif [[ "$result" == "SKIP" ]]; then
    (( SKIP++ )); info "$name — SKIP: $note"
  else
    (( FAIL++ )); fail "$name — FAIL: $note"
  fi
}

# ── chaos_schedule ───────────────────────────────────────────
# Decides which scenarios to run and in what order.
# Prints one scenario ID per line to stdout.
#
# Ordering rationale: gate tests first (no infra disruption),
# then container-kill tests, then destructive (snapshot/network) last.
# Same seed = same shuffle = CI can replay any failure.
#
chaos_schedule() {
  # Safe full-suite order: gate tests first (no infra disruption), then
  # infrastructure-kill tests, then destructive (snapshot/network) last.
  # CM-02 (kill executor) follows CM-01 (kill agent) so the simpler case
  # is confirmed first; CM-07/08 are last because they restore independently.
  local ordered=(CM-05 CM-06 CM-01 CM-02 CM-03 CM-04 CM-07 CM-08)

  # Single named scenario
  if [[ "$SCENARIO" =~ ^CM-[0-9]+$ ]]; then
    echo "$SCENARIO"; return
  fi

  # Random mode: seed RANDOM for reproducibility, Fisher-Yates shuffle, take N
  if (( RANDOM_COUNT > 0 )); then
    RANDOM=$RANDOM_SEED
    local pool=("${ordered[@]}")
    local n=${#pool[@]}
    for (( i=n-1; i>0; i-- )); do
      local j=$(( RANDOM % (i+1) ))
      local tmp="${pool[$i]}"; pool[$i]="${pool[$j]}"; pool[$j]="$tmp"
    done
    local count=$(( RANDOM_COUNT < n ? RANDOM_COUNT : n ))
    for (( i=0; i<count; i++ )); do echo "${pool[$i]}"; done
    return
  fi

  # All mode: print in safe dependency order
  printf '%s\n' "${ordered[@]}"
}

# ── Scenario implementations ─────────────────────────────────

# CM-01: kill aads-agent AFTER snapshot captured, BEFORE step 1 starts.
# Expected: failed_retryable, node_locks=0, no partial state on target.
run_cm01() {
  log "CM-01: agent_killed_mid_execution"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record CM-01 FAIL "no plan generated"; return; }
  info "plan: $plan_id (status=$(sql "SELECT plan_status FROM diagnosis_reports WHERE diagnosis_id='$plan_id';" || true))"

  # Approve
  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-cm01"}' -o /dev/null

  # Start execute in background, then kill agent after 3s (snapshot window)
  local ikey="cm01-$(date +%s)"
  (gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null &)

  sleep 3
  log "CM-01: killing aads-agent on target"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc 'systemctl stop aads-agent'

  # Wait for terminal state
  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  # Restart agent for subsequent tests
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc 'systemctl start aads-agent' 2>/dev/null || true

  local locks; locks="$(sql "SELECT count(*) FROM node_locks;" || true)"

  if [[ "$terminal" == "failed_retryable" && "$locks" -eq 0 ]]; then
    record CM-01 PASS "terminal=$terminal, locks=$locks"
  else
    record CM-01 FAIL "terminal=$terminal (want failed_retryable), locks=$locks (want 0)"
  fi
}

# CM-02: kill the layer4-executor container while step 1 is running.
# Expected: on container restart, executor reads DB state, verifies the step,
#           continues to final_verified / kb_skipped.
run_cm02() {
  log "CM-02: executor_crashed_mid_step"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record CM-02 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-cm02"}' -o /dev/null
  local ikey="cm02-$(date +%s)"
  (gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null &)

  sleep 5
  log "CM-02: stopping layer4-executor container"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab stop layer4-executor 2>/dev/null'

  sleep 3
  log "CM-02: restarting layer4-executor container"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab start layer4-executor 2>/dev/null'

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  if [[ "$terminal" == "kb_skipped" || "$terminal" == "final_verified" || "$terminal" == "kb_imported" ]]; then
    record CM-02 PASS "recovered → $terminal"
  elif [[ "$terminal" == "execution_failed_unknown_state" ]]; then
    record CM-02 PASS "graceful unknown_state (step was non-idempotent mid-run)"
  else
    record CM-02 FAIL "terminal=$terminal (want kb_skipped or unknown_state)"
  fi
}

# CM-03: stop Loki, break nginx, wait — no anomaly should reach Layer 2.
# Expected: no new plan generated (graceful degradation, not a crash).
run_cm03() {
  log "CM-03: loki_unavailable"
  reset_baseline

  # Wait for Layer 2 to finish processing any backlog from prior scenarios
  # before stopping Loki, so the test window is clean.
  log "CM-03: draining Layer 2 backlog (30s quiet period)"
  sleep 30

  log "CM-03: stopping Loki"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab stop loki 2>/dev/null'
  sleep 5  # let Alloy detect the connection is gone before injecting

  # CM-03 measures: does Layer 1 produce NEW anomaly_logs when Loki is unreachable?
  # Layer 2 might still process old anomalies (that's OK); the real question is
  # whether Layer 1 quietly stops ingesting when its Loki source is unavailable.
  local anomaly_before; anomaly_before="$(sql "SELECT count(*) FROM anomaly_logs;" || true)"
  break_nginx_stopped

  local appeared=false
  local deadline=$((SECONDS + 90))
  while (( SECONDS < deadline )); do
    local anomaly_after; anomaly_after="$(sql "SELECT count(*) FROM anomaly_logs;" || true)"
    # If Layer 1 is still somehow ingesting despite Loki being down, that's the failure
    if [[ "$anomaly_after" -gt "$anomaly_before" ]]; then appeared=true; break; fi
    sleep 10
  done

  log "CM-03: restoring Loki"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab start loki 2>/dev/null'

  if [[ "$appeared" == "false" ]]; then
    record CM-03 PASS "no new anomalies ingested while Loki was down"
  else
    record CM-03 FAIL "anomaly_logs grew while Loki was stopped — Layer 1 has alternate path"
  fi
}

# CM-04: stop LiteLLM, break nginx — Layer 2 can't call LLM.
# Expected: no FixingPlan v2 generated (schema 1.0 fallback or nothing).
#           Layer 2 must not crash; it should recover when LiteLLM comes back.
run_cm04() {
  log "CM-04: litellm_down"
  reset_baseline

  log "CM-04: draining Layer 2 backlog (30s quiet period)"
  sleep 30

  log "CM-04: stopping LiteLLM"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab stop litellm 2>/dev/null'

  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"
  local deadline=$((SECONDS + 90))
  local v2_appeared=false

  while (( SECONDS < deadline )); do
    local latest; latest="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"
    if [[ "$latest" != "$before" && -n "$latest" ]]; then
      local sv; sv="$(sql "SELECT schema_version FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1;" || true)"
      [[ "$sv" == "2.0" ]] && v2_appeared=true
      break
    fi
    sleep 10
  done

  log "CM-04: restoring LiteLLM"
  timeout 30 multipass exec "$CONTROLLER" -- bash -lc \
    'cd ~/AADS && sudo docker compose --env-file .env.lab start litellm 2>/dev/null'

  if [[ "$v2_appeared" == "false" ]]; then
    record CM-04 PASS "no schema 2.0 FixingPlan while LiteLLM was down"
  else
    record CM-04 FAIL "schema 2.0 plan appeared while LiteLLM was down"
  fi
}

# CM-05: approve a plan, let the 30-min window expire (via DB manipulation),
# then attempt execute — must get 403.
run_cm05() {
  log "CM-05: approval_expired_before_exec"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record CM-05 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-cm05"}' -o /dev/null

  # Fast-expire: update approved_until to the past directly in DB
  sql "UPDATE plan_approvals SET approved_until = NOW() - INTERVAL '1 minute'
       WHERE plan_id = '$plan_id' AND decision = 'approved';" || true

  local ikey="cm05-$(date +%s)"
  local http_code
  http_code="$(gate POST "/api/plans/$plan_id/execute" \
    -H "Idempotency-Key: $ikey" -o /dev/null -w "%{http_code}")"

  if [[ "$http_code" == "403" ]]; then
    record CM-05 PASS "execute after expiry → 403 ✓"
  else
    record CM-05 FAIL "expected 403, got $http_code"
  fi
  # Clean up: reject the plan so it leaves Queue
  gate POST "/api/plans/$plan_id/reject" -d '{"reason":"chaos-cm05-cleanup"}' -o /dev/null
}

# CM-06: inject two concurrent plans for the same target node.
# Expected: the second mutating step is blocked by node lock (blocked reason: node_locked).
run_cm06() {
  log "CM-06: concurrent_repair_lock"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan1; plan1="$(wait_new_plan "$before")" || { record CM-06 FAIL "no first plan"; return; }

  # Directly inject a live node_lock for the target node to simulate a running mutating step.
  # This is more reliable than trying to time two concurrent executions against a
  # single-instance executor (which processes plans sequentially, not in parallel).
  local target_node_id; target_node_id="$(sql "SELECT target_node_id FROM agent_nodes LIMIT 1;" || true)"
  if [[ -z "$target_node_id" ]]; then
    record CM-06 SKIP "no registered agent node found"; return
  fi

  local injected_lock_id="cm06-$(date +%s)"
  # Inject the lock FIRST, then approve+execute so the executor always sees it.
  # The lock must be inserted before the execution is queued; otherwise the
  # executor's first-pass acquire_lock might run before the INSERT completes.
  sql "INSERT INTO node_locks (node_id, lock_id, plan_id, execution_id, expires_at)
       VALUES ('$target_node_id', '$injected_lock_id', '$plan1', 'cm06-fake-exec',
               NOW() + INTERVAL '120 seconds')
       ON CONFLICT (node_id) DO UPDATE SET
         lock_id=EXCLUDED.lock_id, plan_id=EXCLUDED.plan_id,
         execution_id=EXCLUDED.execution_id, expires_at=EXCLUDED.expires_at, created_at=NOW();" || true
  # Brief pause to ensure the lock row is committed before executor polls
  sleep 2

  gate POST "/api/plans/$plan1/approve" -d '{"reason":"chaos-cm06"}' -o /dev/null
  local ikey1="cm06-$(date +%s)"
  gate POST "/api/plans/$plan1/execute" -H "Idempotency-Key: $ikey1" -o /dev/null

  # Give executor time to pick up the queued execution and hit the lock
  local deadline=$((SECONDS + 60))
  local blocked_seen=false
  while (( SECONDS < deadline )); do
    # executor.py writes policy_decision='node_locked' to audit_events
    local blocked_count
    blocked_count="$(sql "SELECT count(*) FROM audit_events
                          WHERE event_type='policy.blocked'
                          AND policy_decision='node_locked'
                          AND plan_id='$plan1';" || true)"
    [[ "${blocked_count:-0}" -gt 0 ]] && { blocked_seen=true; break; }
    sleep 4
  done

  # Clean up injected lock regardless of result
  sql "DELETE FROM node_locks WHERE lock_id='$injected_lock_id';" || true

  if [[ "$blocked_seen" == "true" ]]; then
    record CM-06 PASS "injected lock → policy.blocked:node_locked ✓"
  else
    local exec_st; exec_st="$(sql "SELECT status FROM plan_executions WHERE plan_id='$plan1' ORDER BY requested_at DESC LIMIT 1;" || true)"
    record CM-06 FAIL "no node_locked audit event (exec status=$exec_st)"
  fi
}

# CM-07: delete the nginx snapshot before execution starts.
# Expected: Knowledge Agent returns blocked:snapshot_failed.
run_cm07() {
  log "CM-07: snapshot_missing"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record CM-07 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-cm07"}' -o /dev/null

  # Remove the snapshot so ensure_known_good_snapshot has nothing to fall back to
  log "CM-07: removing nginx snapshot"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'rm -rf /var/lib/aads-agent/snapshots/nginx'

  local ikey="cm07-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"

  # Restore snapshot for subsequent tests
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc '
    mkdir -p /var/lib/aads-agent/snapshots/nginx
    cp -a /etc/nginx/nginx.conf /var/lib/aads-agent/snapshots/nginx/nginx.conf
    cp -a /etc/nginx/sites-enabled /var/lib/aads-agent/snapshots/nginx/sites-enabled
    chown -R root:aads-agent /var/lib/aads-agent/snapshots/nginx' 2>/dev/null || true

  if [[ "$terminal" == "blocked" ]]; then
    local reason; reason="$(sql "SELECT result->>'reason' FROM plan_executions WHERE execution_id='$exec_id';" || true)"
    if [[ "$reason" == *snapshot* ]]; then
      record CM-07 PASS "blocked:snapshot_failed ✓"
    else
      record CM-07 FAIL "blocked but reason=$reason (want snapshot_failed)"
    fi
  else
    record CM-07 FAIL "terminal=$terminal (want blocked)"
  fi
}

# CM-08: iptables block port 8090 (On-Device Agent) after plan is queued.
# Expected: failed_retryable, node lock released, nginx untouched.
run_cm08() {
  log "CM-08: network_partition (iptables block :8090)"
  reset_baseline
  break_nginx_stopped
  local before; before="$(sql "SELECT COALESCE((SELECT diagnosis_id FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 1),'__none__');" || true)"

  local plan_id; plan_id="$(wait_new_plan "$before")" || { record CM-08 FAIL "no plan generated"; return; }

  gate POST "/api/plans/$plan_id/approve" -d '{"reason":"chaos-cm08"}' -o /dev/null

  # Block outbound from controller to target:8090
  log "CM-08: blocking port 8090 via iptables on target"
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'iptables -I INPUT -p tcp --dport 8090 -j DROP'

  local ikey="cm08-$(date +%s)"
  gate POST "/api/plans/$plan_id/execute" -H "Idempotency-Key: $ikey" -o /dev/null

  local exec_id; exec_id="$(sql "SELECT execution_id FROM plan_executions WHERE plan_id='$plan_id' ORDER BY requested_at DESC LIMIT 1;" || true)"
  local terminal; terminal="$(wait_execution_terminal "$exec_id")"
  local locks; locks="$(sql "SELECT count(*) FROM node_locks;" || true)"

  # Remove firewall rule regardless of result
  timeout 30 multipass exec "$TARGET" -- sudo bash -lc \
    'iptables -D INPUT -p tcp --dport 8090 -j DROP 2>/dev/null || true'

  if [[ "$terminal" == "failed_retryable" && "$locks" -eq 0 ]]; then
    record CM-08 PASS "failed_retryable, no lock leak ✓"
  else
    record CM-08 FAIL "terminal=$terminal (want failed_retryable), locks=$locks (want 0)"
  fi
}

# ── main ──────────────────────────────────────────────────────

# Verify host tooling
if ! bash "$ROOT/scripts/lab/check-prereqs.sh" >/dev/null 2>&1; then
  echo "ERROR: prerequisites check failed — run scripts/lab/check-prereqs.sh for details" >&2
  exit 1
fi

# Verify admin key file exists before any gate() call can use it
if [[ ! -f "$ADMIN_KEY_FILE" ]]; then
  echo "ERROR: admin key not found at $ADMIN_KEY_FILE" >&2
  echo "       Create it with: multipass exec $CONTROLLER -- cat ~/.aads-lab-admin-key > $ADMIN_KEY_FILE" >&2
  exit 1
fi

# Parse CLI
SCENARIO="${1:-all}"
RANDOM_COUNT=0
RANDOM_SEED=$RANDOM

while [[ $# -gt 0 ]]; do
  case "$1" in
    --random) RANDOM_COUNT="$2"; shift 2 ;;
    --seed)   RANDOM_SEED="$2";  shift 2 ;;
    *)        SCENARIO="$1"; shift ;;
  esac
done

log "AADS Chaos E2E — scenario=$SCENARIO random_count=$RANDOM_COUNT seed=$RANDOM_SEED"
log "Controller: $CONTROLLER | Target: $TARGET | Timeout: ${TIMEOUT_SECONDS}s"

# Determine run list via chaos_schedule
RUN_LIST=()
while IFS= read -r line; do RUN_LIST+=("$line"); done < <(chaos_schedule)
log "Run list: ${RUN_LIST[*]}"

for scenario in "${RUN_LIST[@]}"; do
  echo
  log "━━━ $scenario ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  case "$scenario" in
    CM-01) run_cm01 ;;
    CM-02) run_cm02 ;;
    CM-03) run_cm03 ;;
    CM-04) run_cm04 ;;
    CM-05) run_cm05 ;;
    CM-06) run_cm06 ;;
    CM-07) run_cm07 ;;
    CM-08) run_cm08 ;;
    *)     record "$scenario" SKIP "unknown scenario"; ;;
  esac
done

# ── report ────────────────────────────────────────────────────
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  AADS Chaos E2E Results"
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
