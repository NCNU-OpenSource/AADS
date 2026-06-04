# Service-Coverage E2E — Completion Handoff

**Date**: 2026-06-02
**Branch**: `codex/ubuntu-agent-layer0-4`
**Goal**: Extend AADS beyond nginx so it can detect and repair PostgreSQL,
Redis, Docker container, and MySQL/MariaDB failures. Verification lives in
`scripts/lab/e2e-service-repair.sh` (`SR-*` scenarios).

---

## 1. What this track verifies

The existing `chaos-e2e.sh` (`CM-*`) tests **AADS's own resilience** when
controller services, agents, approvals, locks, or upstream dependencies fail.

The service-coverage suite tests the opposite side of the closed loop: can AADS
repair the **business services it monitors** when those services break?

Every supported service follows the same 4-layer pattern:

```text
Layer 1 detect -> Layer 2 diagnose+plan -> pi-agent runner execute -> SR-* verify
Alloy/Loki        FixingPlan 3.0 steps      argv runner on target        E2E result
```

Supported runner operations (Layer 2 (service, operation) -> runner spec; see layer2-analyzer/src/runner_catalog.py):

- nginx: 7
- postgresql: 7
- redis: 7
- docker: 3
- mysql: 7
- system: 1

---

## 2. Current verification status

Final full run:

```text
/tmp/aads-service-repair-final-20260602T115235.log
```

Result:

```text
SR-PG-01  PASS  terminal=kb_skipped, postgresql=active
SR-PG-02  PASS  terminal=kb_skipped, postgresql=active
SR-PG-03  PASS  blocked:snapshot_failed
SR-RD-01  PASS  terminal=kb_skipped, redis=active
SR-RD-02  PASS  terminal=kb_skipped, redis=active
SR-DC-01  SKIP  Docker not installed on aads-target
SR-DC-02  SKIP  Docker not installed on aads-target
SR-MY-01  PASS  terminal=kb_skipped, mysql=active
SR-MY-02  PASS  terminal=kb_skipped, mysql=active

PASS: 7   FAIL: 0   SKIP: 2
```

`kb_skipped` is a successful terminal state in this lab because
`ENABLE_KNOWLEDGE_BASE=false`.

Post-cleanup health check:

```text
postgresql: /var/run/postgresql:5432 - accepting connections
redis: PONG
mysql: Access denied for user 'root'@'localhost' (server reachable)
aads-agent: active
```

The MySQL check reports `Access denied` because the probe intentionally uses
root without credentials; for this lab that still proves the server is reachable.

---

## 3. Manual Gate demo status

Manual Gate repair demo was run after a Gate-only reset. The reset clears
`node_locks` and `diagnosis_reports`; foreign-key cascade clears related Gate
approvals/executions/steps/idempotency records. It intentionally keeps
`raw_logs`, `anomaly_logs`, and `audit_events` for evidence.

For the 2026-06-03 classroom flow, use
`scripts/lab/demo-nginx-manual-gate.sh` instead of the automated
`e2e-service-repair.sh` suite. The helper prepares only the Nginx bad-config
scenario, waits for the restore plan, and stops before approve/execute so the
Dashboard remains the visible control surface.

Successful manual approvals/executions:

| Service demo | Plan | Execution | Command | Terminal |
| --- | --- | --- | --- | --- |
| Nginx bad config | `diag_cluster_0_1780388144` | `exec_bf8fdb55b7e24a96b2359f8400ce9bfa` | `nginx.restore_known_good_config` | `kb_skipped` |
| PostgreSQL stopped | `diag_cluster_0_1780388401` | `exec_5d6c0a63a5244ddb9ce32d4bd541d746` | `postgresql.restart` | `kb_skipped` |
| Redis bad config | `diag_cluster_0_1780388700` | `exec_d545741acc924aee9eff2f343cba09fa` | `redis.restore_known_good_config` | `kb_skipped` |
| MySQL bad config | `diag_cluster_0_1780390317` | `exec_9585cf92a913455a9b023d492a246e81` | `mysql.restore_known_good_config` | `kb_skipped` |

Final MySQL execution evidence:

```text
pre_execution_snapshot:
  mysql.ensure_config_snapshot -> success
  snapshot_refreshed=false existing_snapshot=true service_active=false

step 1:
  mysql.restore_known_good_config -> step_verified
  config_restored=true config_file=/etc/mysql/mysql.conf.d/mysqld.cnf

final_verification:
  mysql.connection_test -> success
  accepting_connections=true
```

Post-demo target health:

```text
nginx: active; nginx -t successful
postgresql: accepting connections
redis: PONG
mysql: active/running; root Access denied means server reachable
aads-agent: active
```

Note: after a successful restore, delayed log windows can still generate a
stale `pending_approval` item for the same service. Treat those as stale if
target health is already good and the latest successful execution is newer than
the injected failure.

---

## 4. Known issue: Nginx partial repair when service is inactive

**Observed**: 2026-06-04 during the manual Nginx demo.

The current `nginx.restore_config` operation can restore a valid
`/etc/nginx/nginx.conf` while still reporting execution failure if Nginx is
already inactive. The wrapper `aads-nginx-restore-known-good` validates the
restored config, then calls `systemctl reload nginx`. When the unit is inactive,
systemd returns:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
nginx.service is not active, cannot reload.
```

In the observed run, the wrapper returned `44`. The target then had a mixed
state:

```text
sudo nginx -t                  -> success
systemctl is-active nginx      -> inactive
curl http://127.0.0.1/         -> 000
```

This is a design issue in the repair contract, not only a one-line wrapper
failure. `restore_config` does not fully represent the desired final state:
config valid, service active, and HTTP reachable. Short-term workaround:
execute a follow-up `nginx.start` plan or run `sudo systemctl start nginx` on
the target after config restore succeeds.

Long-term fix options:

- Make the Nginx restore wrapper state-aware: restore config, run `nginx -t`,
  then `reload` if active or `start` if inactive.
- Or make FixingPlan generation state-transition based: for config incidents,
  emit `restore_config -> start/reload -> config_test -> http_check` instead of
  treating restore as a complete single-step repair.
- Update verification so a repair is not considered complete unless the final
  target state matches the service contract, not merely the per-step command
  outcome.

---

## 5. Deployment workflow

The VMs run deployed copies, so code changes must be deployed before E2E runs.
Use the new deploy helpers instead of repeating manual transfer steps.

Deploy a controller compose service:

```bash
# Default: layer2-analyzer
bash scripts/lab/deploy-controller.sh

# Deploy Layer 1 after filter/cursor changes
AADS_CONTROLLER_SERVICE=layer1-filter bash scripts/lab/deploy-controller.sh
```

Deploy target-side On-Device Agent code, systemd unit, wrappers, sudoers, and
agent env defaults:

```bash
bash scripts/lab/deploy-target.sh
```

The target deploy script also verifies `/v1/node/facts` when `.aads-lab-token`
is present.

---

## 6. Test commands

Full suite:

```bash
bash scripts/lab/e2e-service-repair.sh
```

Single scenario:

```bash
bash scripts/lab/e2e-service-repair.sh SR-RD-02
```

By service:

```bash
bash scripts/lab/e2e-service-repair.sh --service pg
bash scripts/lab/e2e-service-repair.sh --service redis
bash scripts/lab/e2e-service-repair.sh --service docker
bash scripts/lab/e2e-service-repair.sh --service mysql
```

Long runs with saved logs:

```bash
LOG=/tmp/aads-service-repair-$(date +%Y%m%dT%H%M%S).log
stdbuf -oL -eL bash scripts/lab/e2e-service-repair.sh 2>&1 | tee "$LOG"
```

Focused local tests added for the final fixes:

```bash
uv run --with pytest --with asyncpg python -m pytest \
  layer1-filter/tests/test_cursor_timestamp.py \
  layer1-filter/tests/test_pattern_filter.py
```

---

## 7. Fixes completed after the original handoff

### Deployment automation

- Added `scripts/lab/deploy-controller.sh`.
- Added `scripts/lab/deploy-target.sh`.
- Updated `scripts/lab/sync-controller.sh` to remove the remote checkout with
  `sudo rm -rf`, avoiding root-owned Docker pytest cache failures.

### Layer 2 schema and planning

- `PreExecutionSnapshot.scope` now accepts:
  - `nginx_config`
  - `postgresql_config`
  - `redis_config`
  - `mysql_config`
- Service-aware `pre_execution_snapshot` and `final_verification` now cover
  PostgreSQL, Redis, and MySQL.
- Config-error detection uses specific signatures such as `invalid line`,
  `invalid_chaos`, `fatal config file error`, and `can't open config`, avoiding
  false positives from generic `error` or `failed` service-down logs.
- MySQL config-error detection now also recognizes `wrong group definition`,
  `includedir directive`, `invalid datadir`, and `data dir not found`, so bad
  server config routes to `mysql.restore_known_good_config` instead of
  `mysql.restart`.

### pi-agent wrappers

- PostgreSQL restart/restore verify with `pg_isready`, not only the umbrella
  `postgresql.service`.
- PostgreSQL connection probe tries socket first, then `localhost`.
- Redis restart/restore verify with `redis-cli ping`.
- MySQL restart/restore verify with `mysqladmin ping`.
- MySQL probe treats root `Access denied` as server reachable.
- Redis/MySQL snapshot wrappers no longer refresh known-good snapshots from
  invalid-but-nonempty configs.
- MySQL config discovery prefers server config files (`mysqld.cnf`,
  `50-server.cnf`) instead of the first sorted `.cnf`, which can be a client
  config (`mysql.cnf`).
- MySQL snapshot refresh now requires service active, server reachable, and
  config validation success. If MySQL is already failed, the wrapper keeps the
  previous known-good snapshot and returns success without refreshing.

### E2E isolation and cleanup

- Service installation checks use `systemctl cat` instead of active unit lists.
- Reset helpers restore service-native ownership and modes.
- Normal scenarios reset baseline before snapshot creation.
- Stopped/bad-config/no-snapshot scenarios emit unique timestamped markers.
- `wait_new_plan(before, expected_command_prefix)` filters by diagnosis
  timestamp and expected command prefix, preventing stale or unrelated plans
  from satisfying a scenario.
- `SR-PG-03` now restores both the PostgreSQL snapshot and PostgreSQL service
  health after validating `blocked:snapshot_failed`.

### Layer 1 cursor bug

One final full run exposed that Redis bad-config logs reached `raw_logs` but
did not always become anomalies. Root cause: `layer1-filter` queried Loki with
`direction=forward` and `limit=BATCH_SIZE`; when a window contained more than
50 logs, Layer 1 advanced its cursor to the poll end time instead of the newest
fetched log timestamp, skipping the rest of the window.

Fix:

- `layer1-filter/src/main.py` now advances `last_timestamp` to the newest
  fetched log timestamp when logs are returned.
- Added `layer1-filter/tests/test_cursor_timestamp.py`.

Verification:

```text
SR-RD-02 PASS terminal=kb_skipped, redis=active
```

---

## 8. Remaining work

1. Install Docker on `aads-target` and create the allowed `aads-test-nginx`
   container so `SR-DC-01` and `SR-DC-02` can run.
2. Persist currently live-only lab config into repo templates:
   - target Alloy config that tails PostgreSQL, Redis, MySQL, and syslog
   - `.env.lab` multi-service `LAYER1_LOKI_QUERY`
3. Consider reducing Layer 1 lab rebuild time by keeping heavyweight LogBERT
   dependencies out of the default lab image when semantic filtering is disabled.
4. Re-run the full suite after Docker is installed; expected target is
   `PASS: 9 FAIL: 0 SKIP: 0`.
5. Add diagnosis dedupe/cooldown for delayed post-repair logs that create stale
   Gate Queue items after a successful execution.
6. Clarify Layer 2 diagnostic command boundary. The LLM diagnostic tool runs
   inside the analyzer container today, so common host commands like
   `journalctl`, `ps`, `ss`, `docker`, and `curl` may be unavailable. Prefer
   target-side runner probes (argv + extractor) for deterministic evidence.
7. Fix Nginx config repair as a complete state transition. The current
   `restore_config` operation can leave Nginx inactive after config repair if
   the wrapper tries to reload an inactive unit.

---

## 9. Useful debug commands

Gate-only reset for manual demos:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -c \
  \"DELETE FROM node_locks; DELETE FROM diagnosis_reports;\""
```

One-click Nginx manual Dashboard demo:

```bash
bash scripts/lab/demo-nginx-manual-gate.sh
bash scripts/lab/demo-nginx-manual-gate.sh --verify <DIAGNOSIS_ID>
```

Inspect execution result:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -At -c \
  \"SELECT result::text FROM plan_executions WHERE plan_id='<PLAN>' ORDER BY requested_at DESC LIMIT 1;\""
```

Inspect generated plans:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -P pager=off -c \
  \"SELECT diagnosis_id, timestamp, LEFT(action_plan::text, 260) FROM diagnosis_reports ORDER BY timestamp DESC LIMIT 20;\""
```

Confirm Layer 1 saw a marker:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -P pager=off -c \
  \"SELECT time, source, LEFT(message, 240) FROM raw_logs WHERE message ILIKE '%<MARKER>%' ORDER BY time DESC;\""
```

Confirm anomaly promotion:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -P pager=off -c \
  \"SELECT time, service, LEFT(raw_message, 240) FROM anomaly_logs WHERE raw_message ILIKE '%<MARKER>%' ORDER BY time DESC;\""
```

Layer 2 crash logs:

```bash
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  logs --tail=50 layer2-analyzer | grep -E 'ERROR|Exception|Pydantic'"
```

Target health:

```bash
multipass exec aads-target -- bash -lc '
pg_isready || true
redis-cli ping || true
mysqladmin -u root --connect-timeout=5 ping || true
systemctl is-active aads-agent || true
'
```
