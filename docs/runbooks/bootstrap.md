# Day-0 Bootstrap Runbook

This runbook brings up the v4.2 AADS lab topology:

- macOS admin workstation using OrbStack for local container debugging.
- Ubuntu controller VM running the AADS Docker Compose stack, Gate, System Agent, and Knowledge Agent.
- Ubuntu target VM running nginx, PostgreSQL, Redis, MySQL/MariaDB, telemetry, and On-Device Agent.

Production-like runtime belongs on Ubuntu. macOS is only the admin/dev surface.

Current demo Dashboard / Gate Console:

```text
http://100.72.172.83:5000/
```

Current demo lab endpoints:

| Service | URL |
| --- | --- |
| Gate Console / Dashboard | `http://100.72.172.83:5000/` |
| Grafana | `http://100.72.172.83:3000/` |
| Prometheus | `http://100.72.172.83:9090/` |
| Loki | `http://100.72.172.83:3100/` |
| Layer 2 Analyzer | `http://100.72.172.83:8080/` |
| Target On-Device Agent | `http://100.77.197.118:8090/` |

## Prerequisites

- macOS with Multipass installed.
- OrbStack installed for local image/container debugging.
- SSH access to the Multipass VMs.
- AADS repository available on the controller VM.
- A private network path from controller VM to target VM.
- LiteLLM upstream credentials exported in the shell running the lab bootstrap:

```bash
export AADS_LITELLM_UPSTREAM_API_BASE=https://o.virtualtips.info/v1
export AADS_LITELLM_UPSTREAM_API_KEY=...
export LITELLM_MODEL=gpt-5.5
```

`scripts/lab/up.sh` fails fast when the upstream key is missing instead of writing an unusable empty value into `.env.lab`. When `.env.lab` already exists, it preserves the LiteLLM master key, model, upstream base URL, and upstream key unless explicit environment variables override them.

Run:

```bash
scripts/lab/check-prereqs.sh
```

## Bootstrap Order

1. Create the controller VM and target VM.
2. Generate a stable target `node_id` and store it at `/etc/aads-agent/node-id`.
3. Generate the static pi-agent bearer token on the controller.
4. Install the token into controller `.env.lab` and target `/etc/aads-agent/agent.env`.
5. Install nginx/PostgreSQL/Redis/MySQL as needed, root-owned wrappers, sudoers, pi-agent, and the systemd unit on the target.
6. Install target Alloy log forwarding to push nginx/PostgreSQL/Redis/MySQL/syslog logs into controller Loki.
7. Create the first known-good service snapshots under `/var/lib/aads-agent/snapshots/{nginx,postgresql,redis,mysql}/`.
8. Start On-Device Agent and verify `/health` fails fast if wrappers, sudoers, or snapshot are unsafe.
9. Install Docker and Compose on the controller VM, then set Docker daemon DNS to `AADS_DOCKER_DNS_PRIMARY` and `AADS_DOCKER_DNS_SECONDARY` (defaults: `1.1.1.1`, `8.8.8.8`). Multipass NAT DNS can resolve from the VM while failing inside Docker build containers.
10. Start the controller compose stack.
11. Register the target with the controller using the `node_id`, environment, `agent_version`, base URL, and `runner_capabilities` returned by `/v1/node/facts`.
12. Run baseline health and no-anomaly tests before injecting broken states.

Restore runners must return a blocked result if the required known-good snapshot does not exist.

## Agent Execution Model

- `System Agent` (Layer 2) emits executable `FixingPlan` JSON with `schema_version="3.1"`
  **only**. The 3.1 schema requires an `execution_profile` manifest
  (`layer2-analyzer/src/schemas/action_plan.py` pins `schema_version` to the literal
  `"3.1"` and makes `execution_profile` a required field). Schema `3.0` is
  **legacy-accepted on the consumer side only** — `validate_plan`
  (`layer4-executor/src/executor.py`) and the Dashboard
  (`SUPPORTED_EXECUTION_SCHEMAS = {'3.0', '3.1'}`) still parse a stored 3.0 plan,
  but Layer 2 no longer produces one.
- `Gate` approves, rejects, or queues execution through `POST /api/plans/{plan_id}/execute`.
- `Knowledge Agent` runs on the controller and calls the target On-Device Agent one runner step at a time. It does not push the whole plan to the target.
- `On-Device Agent` remains stateless for execution. It exposes `/health`, `/v1/node/facts`, `/v1/commands/run`, and the reserved `/v1/agent-tasks/run` RCA dispatch contract. Legacy `/v1/probes/run` and `/v1/actions/run` are removed in V2.
- Verification and final verification must be runner probes with structured extractor results. Free text and LLM judgement are not valid execution checks.

## Execution Security Invariants

The execution path is hardened across three coupled ADRs (ADR-005/006/007; see
`docs/SECURITY_HARDENING_AUDIT.md`). These hold by default in the lab — do not
disable them for a demo.

- **ExecutionProfile / PolicyCard (ADR-005)**: every 3.1 FixingPlan carries an
  `execution_profile` manifest generated **deterministically** from the runner
  catalog (`layer2-analyzer/src/runner_catalog.py::execution_profile_for`). The
  LLM never contributes to it. The On-Device Agent's `PolicyCard`
  (`pi-agent/src/safety_cards/policy_card.py`) enforces the manifest
  **fail-closed**: `AADS_POLICY_MODE` defaults to `enforce` (code default — it is
  not set in `.env.example` or compose), so a step outside the manifest is denied.
  `AADS_POLICY_MODE=audit` only warns-but-runs and is not the lab default.
- **Drift detection (ADR-006)**: `plan_sha256` binds execution to the exact
  approved plan (TOCTOU guard). The Dashboard and executor compute this hash with
  byte-identical logic; a mismatch pauses execution with
  `drift_type='approved_plan_hash_mismatch'` instead of running a tampered plan.
- **Pause and resume**: `paused_for_review` is a **non-terminal, recoverable**
  state, not a failure. Failures route through `classify_step_failure`
  (hook_denied / verification_failed / retries_exhausted) and record a row in
  `execution_escalations` (migration 005). The Dashboard surfaces these as
  **resume or abort** — an operator resumes after fixing the cause or aborts to
  end the run. `failed_retryable` is likewise non-terminal.
- **Log-injection defence (ADR-007)**: `layer2-analyzer/src/log_guard.py` is a
  deterministic scanner + data-fence + taint registry. Log data is untrusted
  input to the LLM; a tainted diagnosis is forced into human review
  (`auto_execute_allowed=False`, `requires_approval=True`,
  `security_review_required=True`) and can never auto-execute.
- **Admin auth**: approve / execute and other mutating Gate actions require the
  `AADS_ADMIN_API_KEY`, passed in the `X-Admin-API-Key` request header. GET
  read endpoints are unauthenticated; only POST mutations call `require_admin`.

## Commands

```bash
scripts/lab/up.sh
scripts/lab/sync-controller.sh
scripts/lab/deploy-controller.sh
scripts/lab/deploy-target.sh
scripts/lab/demo-nginx-manual-gate.sh
scripts/lab/chaos-e2e.sh
scripts/lab/e2e-service-repair.sh
scripts/lab/down.sh
```

If Docker builds fail with `Temporary failure in name resolution` during `pip install`, rerun `scripts/lab/up.sh` or set:

```bash
AADS_DOCKER_DNS_PRIMARY=1.1.1.1 AADS_DOCKER_DNS_SECONDARY=8.8.8.8 scripts/lab/up.sh
```

Use `scripts/lab/sync-controller.sh` to copy the current workstation checkout to `aads-controller:~/AADS`. The script disables macOS AppleDouble resource-fork files, deletes any `._*` files on the controller so Grafana provisioning does not try to parse files like `._dashboards.yaml`, and removes an existing remote checkout with `sudo rm -rf` so root-owned Docker pytest caches do not block sync.

Use `scripts/lab/deploy-controller.sh` after changing controller-side code baked into Docker images. It defaults to `layer2-analyzer`; set `AADS_CONTROLLER_SERVICE=layer1-filter` when deploying Layer 1 filter changes.

Use `scripts/lab/deploy-target.sh` after changing `pi-agent/`, wrappers, sudoers expectations, or the target systemd unit. The script packages target-side files, installs them under `/opt/aads-agent` and `/usr/local/sbin`, restarts `aads-agent`, and verifies `/v1/node/facts` when `.aads-lab-token` is present.

`scripts/lab/e2e-service-repair.sh` resets target service configs from known-good snapshots before injected scenarios and verifies service recovery through the Gate/Layer 4 path. It covers PostgreSQL, Redis, Docker, and MySQL/MariaDB scenarios; Docker cases skip until Docker and the allowlisted `aads-test-nginx` container exist on the target.

`scripts/lab/demo-nginx-manual-gate.sh` is the one-click classroom demo helper for the Nginx bad-config scenario. It verifies lab policy, rejects stale pending/approved Gate items, restores a known-good Nginx baseline, injects a bad `nginx.conf`, waits for a restore plan (the script's schema filter accepts `3.0` or `3.1`; Layer 2 emits `3.1`), and then stops. The operator must still click Approve and Execute in the Dashboard.

## Manual Gate Demo Workflow

Use this flow when the goal is to watch the repair loop through the dashboard,
not just run the automated PASS report.

1. For the classroom Nginx demo, run:

```bash
bash scripts/lab/demo-nginx-manual-gate.sh
```

The script performs the Gate-visible reset by rejecting stale pending/approved
items, verifies the target baseline, injects the bad Nginx config, and waits for
a new restore plan (schema `3.1`; the script's schema filter accepts `3.0` or
`3.1`, so the manual-gate steps work against either). It intentionally does not
approve or execute.

2. Open the Dashboard:

```text
http://100.72.172.83:5000/
```

3. Manually press Approve, then Execute for the restore plan shown by the
script. Prefer a plan whose runner contains `aads-nginx-restore-known-good` or
`nginx.restore_config`; do not execute a stale `nginx.start` item if one appears.
Approve and Execute are admin-gated: the Dashboard sends `AADS_ADMIN_API_KEY` in
the `X-Admin-API-Key` header, so the configured admin key must match.

   If a step trips a safety card or a verification probe, the execution enters
   `paused_for_review` (non-terminal) and the Dashboard shows **Resume** /
   **Abort** instead of a terminal result. This is the normal recoverable path,
   not a crash — fix the underlying cause and resume, or abort to end the run.
   The pause is recorded in `execution_escalations`.

4. Verify after Execute:

```bash
bash scripts/lab/demo-nginx-manual-gate.sh --verify <DIAGNOSIS_ID>
```

5. For multi-service manual demos, clear only Gate-visible state if needed:

```sql
DELETE FROM node_locks;
DELETE FROM diagnosis_reports;
```

`diagnosis_reports` cascades to approvals, executions, execution steps, and
plan idempotency records. Do not clear `raw_logs`, `anomaly_logs`, or
`audit_events`; they are the evidence trail.

## Idempotency And Expiry

- Plan approvals expire after 30 minutes.
- Expiry is checked only when execution starts; in-flight commands rely on command timeout.
- `POST /execute` requires `Idempotency-Key`.
- Same key + same plan returns the original execution.
- Same key + different plan returns `400`.
- Same plan + different key within the 30-minute idempotency TTL returns `409`.
- AgentTask dispatch follows the same idempotency TTL pattern.

## Rollback And Recovery

- Legacy nginx-only repair plans call `nginx.ensure_known_good_snapshot` before the first step.
- Legacy v1 snapshot scope is nginx config only: `/etc/nginx/nginx.conf` and `/etc/nginx/sites-enabled`.
- Any aborted legacy nginx step with snapshot enabled triggers `nginx.restore_known_good_config`.
- Controller restart recovery is DB-driven: verified steps are skipped, a running step is verified first, and uncertain state becomes `execution_failed_unknown_state`.
- For service-coverage v2 plans, snapshot and final verification are service-aware
  (`postgresql_config`, `redis_config`, `mysql_config`, etc.).
- MySQL snapshot refresh must only happen from a healthy baseline: service active,
  `mysqladmin` reachable, and config validation successful. If MySQL is already
  failed, keep the previous known-good snapshot and allow restore to use it.

## macOS Development

- Use OrbStack for local image build, image inspection, and single-container debugging.
- Use Multipass for full compose stack testing: one controller VM and one target VM.
- Do not rely on macOS Docker behavior for production parity.
- GPU/DCGM is optional in lab; start DCGM only with the `gpu` compose profile on hosts with NVIDIA runtime. The default Layer 1 container runs without NVIDIA device reservations so Multipass CPU-only labs can start.

## 2026-06-02 Service-Coverage E2E Evidence

Validated service-coverage run:

```text
/tmp/aads-service-repair-final-20260602T115235.log
```

| Scenario | Expected result | Final result |
| --- | --- | --- |
| `SR-PG-01` | PostgreSQL stopped -> restart -> active | PASS `terminal=kb_skipped` |
| `SR-PG-02` | PostgreSQL bad config -> restore -> active | PASS `terminal=kb_skipped` |
| `SR-PG-03` | Missing PostgreSQL snapshot -> block | PASS `blocked:snapshot_failed` |
| `SR-RD-01` | Redis stopped -> restart -> active | PASS `terminal=kb_skipped` |
| `SR-RD-02` | Redis bad config -> restore -> active | PASS `terminal=kb_skipped` |
| `SR-DC-01` | Docker container stopped -> restart | SKIP Docker not installed |
| `SR-DC-02` | Non-allowlisted container denied | SKIP Docker not installed |
| `SR-MY-01` | MySQL stopped -> restart -> active | PASS `terminal=kb_skipped` |
| `SR-MY-02` | MySQL bad config -> restore -> active | PASS `terminal=kb_skipped` |

Summary:

```text
PASS: 7   FAIL: 0   SKIP: 2
```

Post-run target health:

```text
postgresql: /var/run/postgresql:5432 - accepting connections
redis: PONG
mysql: Access denied for user 'root'@'localhost' (server reachable)
aads-agent: active
```

`kb_skipped` is expected in this lab because `ENABLE_KNOWLEDGE_BASE=false`.

## 2026-06-02 Manual Gate Demo Evidence

The per-service manual-gate run table (Nginx / PostgreSQL / Redis / MySQL plan,
execution, command, terminal) lives in
[`docs/service-coverage-handoff.md` §3](../service-coverage-handoff.md#3-manual-gate-demo-status)
and is not duplicated here. That section also records the MySQL final
verification (`accepting_connections=true`, `mysql.service` active, injected
`datadir=/nonexistent/...` marker removed from `mysqld.cnf`).

## 2026-05-27 Multipass E2E Evidence

Lab topology:

- `aads-controller`: `192.168.252.2`
- `aads-target`: `192.168.252.3`
- target `node_id`: `128d7819-9c41-45e7-ba08-ad1dd3a14b06`

Validated scenarios:

| Scenario | Execution | Expected command | Terminal state |
| --- | --- | --- | --- |
| `nginx_stopped` | `exec_fdbc78dff6de447a8d59d14ab01a0f72` | `nginx.start` | `kb_skipped` |
| `nginx_bad_config` | `exec_182f48227cfb42f591ec2b5783c6bad5` | `nginx.restore_known_good_config` | `kb_skipped` |

Both scenarios restored target HTTP health and `nginx -t` succeeded after repair. `kb_skipped` is expected in this lab because `ENABLE_KNOWLEDGE_BASE=false`.
The final `nginx_stopped` run also verified that the pre-execution snapshot trace is marked `step_verified` for `step_id=0`.
