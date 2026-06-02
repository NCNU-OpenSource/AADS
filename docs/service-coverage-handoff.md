# Service-Coverage E2E — Handoff for Codex

**Date**: 2026-06-02
**Branch**: `codex/ubuntu-agent-layer0-4`
**Goal**: Extend AADS beyond nginx so it can detect & repair PostgreSQL, Redis,
Docker containers, and MySQL/MariaDB failures. Verified by a new
`scripts/lab/e2e-service-repair.sh` suite (scenarios `SR-*`).

---

## 1. What this work is

The existing `chaos-e2e.sh` (CM-01..08, all PASS) tests **AADS's own
resilience** — what happens when AADS components die. This new track tests the
**opposite**: can AADS *repair the business services it monitors* when they
break? We added a 4th service tier and a parallel test suite.

The repair pipeline for every service follows the same 4-layer pattern:

```
Layer 1 (detect)  →  Layer 2 (diagnose+plan)  →  pi-agent catalog (execute)  →  E2E test (verify)
   Alloy/Loki         FixingPlan v2 steps         sudo wrappers on target        SR-* scenarios
```

---

## 2. What is DONE (committed)

| Commit | Content |
|--------|---------|
| `808230b` | Phase 1 — PostgreSQL: 5 wrappers, 7 catalog cmds, Layer 2 prompt, SR-PG-01/02/03 |
| `4533613` | Phase 2/3/4 — Redis (5 wrappers), Docker container (2 wrappers, allowlist), MySQL (5 wrappers); 25 catalog cmds total; SR-RD/DC/MY scenarios |
| `d981d29` | Layer 2 `_ensure_lab_node_agent_steps` extended to PG/Redis/MySQL keyword detection |
| `a8e05a9` | Layer 2 Pydantic fix: empty `affected_service` / `root_cause` no longer crash plan generation |
| `c50d22d` | systemd unit: `/etc/postgresql /etc/redis /etc/mysql` + log dirs added to `ReadWritePaths` |
| `5a097d9` | restore-config wrappers: restore service-native file ownership after restore; `ReadOnlyPaths` for data dirs |
| `da9ad94` | Layer 2: service-aware `final_verification` + `pre_execution_snapshot` + tightened `is_config_error` |

**pi-agent catalog now has 32 commands** (verified live via `/v1/node/facts`):
nginx (7), postgresql (7), redis (7), docker (3), mysql (7), system (1).

---

## 3. Current test status (last full run, BEFORE commit da9ad94)

```
SR-PG-01  FAIL  terminal=blocked, postgresql=inactive   → restored_config_invalid
SR-PG-02  FAIL  terminal=blocked, postgresql=active
SR-PG-03  FAIL  blocked but reason= (want snapshot_failed)
SR-RD-01  FAIL  terminal=execution_failed, redis=active  → repair worked, final verify (nginx) failed
SR-RD-02  FAIL  terminal=execution_failed, redis=active
SR-DC-01  SKIP  Docker not installed
SR-DC-02  SKIP  Docker not installed
SR-MY-01  SKIP  MySQL not installed (was active earlier; flaky detection)
SR-MY-02  SKIP  MySQL not installed
```

Commit `da9ad94` is expected to fix SR-RD-01/02 (service-aware final verify),
SR-PG-01 (stop→restart not restore; restart-based config validation), and
SR-PG-03 (service-aware snapshot guard). **NOT yet re-tested — see step 4.**

---

## 4. CRITICAL: deploy step before next test run

Code is committed to git but the **VMs run deployed copies**. After any change
you MUST redeploy. The deploy is NOT a single script yet — this is the gap.

```bash
# (a) sync repo to controller
bash scripts/lab/sync-controller.sh

# (b) REBUILD layer2-analyzer (force-recreate is NOT enough — code is baked into image)
multipass exec aads-controller -- bash -lc \
  'cd ~/AADS && sudo docker compose --env-file .env.lab build layer2-analyzer && \
   sudo docker compose --env-file .env.lab up -d layer2-analyzer'

# (c) deploy pi-agent wrappers to target (NO deploy script exists — manual transfer)
for w in aads-postgresql-restart aads-postgresql-reload aads-postgresql-config-test \
         aads-postgresql-ensure-config-snapshot aads-postgresql-restore-config \
         aads-redis-restart aads-redis-reload aads-redis-config-test \
         aads-redis-ensure-config-snapshot aads-redis-restore-config \
         aads-docker-container-restart aads-docker-container-start \
         aads-mysql-restart aads-mysql-reload aads-mysql-config-test \
         aads-mysql-ensure-config-snapshot aads-mysql-restore-config; do
  multipass transfer "pi-agent/wrappers/$w" "aads-target:/tmp/$w"
done
multipass exec aads-target -- sudo bash -lc \
  'for w in /tmp/aads-postgresql-* /tmp/aads-redis-* /tmp/aads-docker-* /tmp/aads-mysql-*; do
     cp "$w" /usr/local/sbin/ && chmod 0755 /usr/local/sbin/$(basename "$w"); done'

# (d) deploy pi-agent main.py + systemd unit (if changed), restart agent
multipass transfer pi-agent/src/main.py aads-target:/tmp/main.py
multipass exec aads-target -- sudo bash -lc 'cp /tmp/main.py /opt/aads-agent/main.py && systemctl restart aads-agent'
# systemd unit is at /etc/systemd/system/aads-agent.service (see commit 5a097d9 for exact content)
```

**ACTION ITEM**: write `scripts/lab/deploy-target.sh` to automate (c)+(d) and
`scripts/lab/deploy-controller.sh` wrapping (a)+(b). This was the single biggest
time sink — manual deploy caused several false-failure test runs.

---

## 5. Infra state on target VM (aads-target, 192.168.252.3)

- **Installed & active**: nginx, postgresql@16-main, redis-server. MySQL was
  installed but flaky (`systemctl` detection inconsistent — sometimes reports
  not-installed). Docker NOT installed.
- **Snapshots** at `/var/lib/aads-agent/snapshots/{postgresql,redis,mysql}/`
  (owner `root:aads-agent`, mode 0640).
- **sudoers** `/etc/sudoers.d/aads-agent` has all 22 wrapper grants (docker uses
  trailing `*` for the container arg).
- **agent.env** has `AADS_ALLOWED_JOURNAL_UNITS` (incl. pg/redis/mysql) and
  `AADS_DOCKER_ALLOWED_CONTAINERS=aads-test-nginx`.
- **Alloy** `/etc/alloy/config.alloy` now tails pg/redis/mysql/syslog logs (not
  committed to git — it's a target-side file; consider capturing it in the repo).
- **Layer 1** `.env.lab` `LAYER1_LOKI_QUERY` changed to
  `{node_id="128d7819-9c41-45e7-ba08-ad1dd3a14b06"}` so it queries ALL services,
  not just `{source="target-nginx"}`. **`.env.lab` is gitignored** — change is
  live only; document or template it.

---

## 6. Remaining TODO (priority order)

1. **Re-run after deploying `da9ad94`**: `bash scripts/lab/e2e-service-repair.sh`
   — expect SR-PG-01/02/03 and SR-RD-01/02 to flip to PASS. Verify, debug any
   residual failures with:
   `SELECT result::text FROM plan_executions WHERE plan_id='<id>' ORDER BY requested_at DESC LIMIT 1;`

2. **Test isolation / stale-anomaly contamination**: scenarios share a Loki
   lookback window. Bad-config log lines from SR-PG-02 can still be in-window
   when SR-PG-01 runs next, contaminating its anomaly cluster and skewing the
   restart-vs-restore decision. Consider: per-scenario unique markers, or a
   longer quiet/drain period, or narrowing the cluster time window.

3. **Install MySQL reliably on target** (or gate SR-MY behind a clear
   precondition). `mysql_installed()` detection in the script is flaky.

4. **Docker scenarios**: install Docker on target, create `aads-test-nginx`
   container, then SR-DC-01/02 can run. Currently always SKIP.

5. **Persist target-side config in repo**: Alloy config and `.env.lab`
   `LAYER1_LOKI_QUERY` are live-only. Template them so a fresh `up.sh` reproduces
   the multi-service setup.

6. **Deploy automation** (see §4) — write the two deploy scripts.

7. **Docs**: once green, write a Technical-Report in `docs/obsidian-vault/` and
   run `sync-obsidian` (per CLAUDE.md rule).

---

## 7. Key debugging commands

```bash
# What did an execution actually do / why blocked:
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -At -c \
  \"SELECT result::text FROM plan_executions WHERE plan_id='<PLAN>' ORDER BY requested_at DESC LIMIT 1;\""

# Is the generated plan schema 2.0 with steps (approvable)?  Empty/404 = fallback plan = Layer 2 crashed:
curl -s -H "X-Admin-API-Key: $(tr -d '\n' < .aads-lab-admin-key)" \
  http://192.168.252.2:5000/api/plans/<PLAN>/approve -X POST -d '{"reason":"x"}' -H 'Content-Type: application/json'

# Layer 2 crash logs (Pydantic etc.):
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  logs --tail=30 layer2-analyzer | grep -E 'ERROR|Exception|Pydantic'"

# Long test runs: use nohup (Bash tool caps at 10min) and watch the log:
nohup bash scripts/lab/e2e-service-repair.sh > /tmp/sr.log 2>&1 & echo $!
```

**Gotcha**: `sql()` in the test scripts has `timeout 30` — concurrent hung
`multipass exec` calls can exhaust SSH slots. Don't launch background DB queries
that may hang; they block subsequent calls.
