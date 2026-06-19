# AADS - AI Auto Debug System

AADS is a server + on-device agent system for collecting service logs,
detecting anomalies, generating root-cause analysis, and executing approved
repair plans through a controlled runner.

The recommended deployment path is the release installer pair:

- `install-server.sh` on the controller/server host.
- `install-agent.sh` on every machine that should be monitored and repaired.

For the longer deployment guide, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Quick Start

### 1. Install the Server

Run this on the controller host. The installer downloads the release bundle,
starts the Docker Compose stack, generates secrets, and prints the matching
agent install command.

```bash
curl -fsSL https://github.com/NCNU-OpenSource/AADS/releases/latest/download/install-server.sh | bash
```

For non-interactive setup, provide the upstream LLM key:

```bash
curl -fsSL https://github.com/NCNU-OpenSource/AADS/releases/latest/download/install-server.sh | \
  AADS_LITELLM_UPSTREAM_API_KEY=sk-... bash
```

Useful server environment variables:

| Variable | Purpose | Default |
| --- | --- | --- |
| `AADS_INSTALL_DIR` | Server bundle install path | `$HOME/aads`, or `/opt/aads` as root |
| `AADS_RELEASE_BASE_URL` | Release asset base URL | GitHub latest release |
| `AADS_LITELLM_UPSTREAM_API_KEY` | Required upstream LLM API key | prompted |
| `LITELLM_MODEL` | Model used through LiteLLM | `gpt-5.5` |
| `AADS_ADMIN_API_KEY` | Dashboard/Gate admin key | generated |
| `PI_AGENT_TOKEN` | Shared server-to-agent bearer token | generated |
| `TIMESCALEDB_PASSWORD` | TimescaleDB password | generated |
| `AADS_POLICY_MODE` | On-device PolicyCard mode: `enforce` (fail-closed) or `audit` (warn-only) | `enforce` |
| `ENABLE_KNOWLEDGE_BASE` | Import successful repairs into the knowledge base | `false` (lab) |

### 2. Install an On-Device Agent

Run this on each target host. The server bootstrap prints a fully populated
command; the shape is:

```bash
curl -fsSL https://github.com/NCNU-OpenSource/AADS/releases/latest/download/install-agent.sh | sudo \
  AADS_SERVER=<server-ip> \
  AADS_AGENT_TOKEN=<token-from-server> \
  AADS_ADMIN_API_KEY=<admin-key-from-server> \
  AADS_RELEASE_BASE_URL=https://github.com/NCNU-OpenSource/AADS/releases/latest/download bash
```

For an air-gapped or lab install, the Dashboard can serve the installer and
payload directly:

```bash
curl -fsSL http://<server>:5000/install-agent.sh | sudo \
  AADS_SERVER=<server> \
  AADS_AGENT_TOKEN=<token-from-server> \
  AADS_ADMIN_API_KEY=<admin-key-from-server> bash
```

The agent installer installs the `aads-agent` systemd service, the `runner.v1`
Command Runner, root wrapper scripts, optional Alloy log forwarding, and then
registers the node with the server.

### 3. Verify

```bash
# Server — GET endpoints are read-only and need no admin key
docker compose -f docker-compose.prod.yaml --env-file .env ps
curl http://<server>:5000/api/agents

# Target
curl -H "Authorization: Bearer <token>" http://<target>:8090/v1/node/facts
systemctl is-active aads-agent alloy
```

## Package And Version Requirements

Runtime requirements:

| Component | Version / source |
| --- | --- |
| Docker Compose | v2 |
| Custom AADS images | `ghcr.io/ncnu-opensource/aads-*:${AADS_IMAGE_TAG:-latest}` |
| Python service base image | `python:3.11-slim` |
| Loki | `grafana/loki:3.6.0` |
| TimescaleDB | `timescale/timescaledb:latest-pg16` |
| LiteLLM | `ghcr.io/berriai/litellm:main-stable` |
| DCGM exporter | `nvcr.io/nvidia/k8s/dcgm-exporter:3.3.8-3.6.0-ubuntu22.04` |

Important Python pins:

| Package | Version |
| --- | --- |
| FastAPI | `0.109.0` |
| Uvicorn | `0.27.0` |
| asyncpg | `0.29.0` |
| aiohttp | `3.9.3` |
| Pydantic | controller services `2.7.4`; pi-agent `>=2.12,<3` |
| OpenAI SDK | `1.60.0` |
| LangChain | `0.3.18` |
| LangGraph | `0.2.60` |
| Flask | `3.0.0` |
| Torch | `2.1.0` |
| Transformers | `4.35.0` |

Release tags publish the server bundle, agent payload, installers, and GHCR
images. For reproducible deployments, pin `AADS_IMAGE_TAG` to a release tag
instead of using `latest`.

## Architecture

```text
Target Host(s)                                                  Server / Controller
────────────────────────────────────────────────────────────────────────────────────────

Layer 0  Alloy tails service logs ───────────────┐              Loki + Prometheus
         nginx / PostgreSQL / Redis / MySQL      ├────────────► log-archiver
         syslog / journal                        │              TimescaleDB raw_logs
                                                  │
Layer 1                                           └────────────► layer1-filter
                                                                 pattern filter + LogBERT
                                                                 anomaly_logs

Layer 2                                                          System Agent
                                                                 consumes anomalies
                                                                 queries Loki/Prometheus
                                                                 RCA through LiteLLM/LLM
                                                                 emits FixingPlan 3.1
                                                                 (+ execution_profile)

Layer 3                                                          Dashboard / Gate
                                                                 admin-key approval
                                                                 notification surface
                                                                 plan queue

Layer 4  On-Device Agent ◄────────────────────────────────────── Knowledge Agent
         runner.v1 HTTP API                                     layer4-executor
         argv-first Command Runner                              node locks
         PolicyCard + root wrappers                             plan_sha256 drift guard
         + audit hook                                           per-step execution
```

Main server ports:

| Service | Port | Purpose |
| --- | --- | --- |
| Dashboard / Gate | `5000` | Queue, approval, execution trace, agent registration |
| Grafana | `3000` | Observability dashboards |
| Prometheus | `9090` | Metrics |
| Loki | `3100` | Log query and ingestion |
| Layer 2 Analyzer | `8080` | System Agent health/API |
| Ingester | `8000` | Anomaly ingestion helper |
| LiteLLM proxy | `4000` | LLM gateway — all model calls route here |
| TimescaleDB | `5432` | Persistent raw/anomaly/diagnosis DB |

The **On-Device Agent** (`runner.v1`) listens on `8090` on each target host — it is
installed by the agent installer, not part of the server Compose stack. The optional
GPU LogBERT profile also exposes cAdvisor (`8081`) and the DCGM exporter (`9400`).

## Agents And Connections

AADS currently uses three agent roles plus the Dashboard Gate:

| Agent | Runs on | Responsibility |
| --- | --- | --- |
| System Agent | Server, `layer2-analyzer` | Consumes `anomaly_logs`, clusters events, queries Loki/Prometheus, calls LiteLLM/LLM for RCA, and produces `FixingPlan 3.1` (with the required `execution_profile` manifest). |
| Knowledge Agent / Executor | Server, `layer4-executor` | Polls approved/queued plans from TimescaleDB, holds node locks, executes steps in order, records audit/execution state. |
| On-Device Agent | Target host, `aads-agent` | Exposes `runner.v1`, receives per-step runner requests, executes argv-first commands through hooks and root wrappers. |

Connection model:

- Target Alloy pushes logs to Server Loki.
- Layer 1 reads Loki and writes anomalies into TimescaleDB.
- System Agent reads anomalies and stores diagnosis reports + FixingPlans.
- Dashboard uses `AADS_ADMIN_API_KEY` for admin actions such as approve,
  reject, execute, and `/api/agents/register`.
- Knowledge Agent uses `PI_AGENT_TOKEN` to call target
  `/v1/commands/run`.
- On-Device Agent reports `runner_capabilities.schema_version="runner.v1"`
  from `/v1/node/facts`.

The `runner.v1` model intentionally avoids a static command catalog as the
safety boundary. Runner requests carry argv, context, and side-effect metadata;
the on-device **safety cards** make contextual allow/deny decisions before
execution. The `PolicyCard` is shipped and **fail-closed by default**
(`AADS_POLICY_MODE=enforce`) — see the Security model below.

## Security model

The execution path is hardened by three coupled ADRs (full write-up in
[docs/SECURITY_HARDENING_AUDIT.md](docs/SECURITY_HARDENING_AUDIT.md); decision
records ADR-005/006/007 under `docs/obsidian-vault/ADR/`):

- **ExecutionProfile / PolicyCard (ADR-005)** — every `FixingPlan 3.1` carries a
  required `execution_profile` manifest, generated deterministically from the
  runner catalog (never by the LLM). The on-device `PolicyCard` enforces it
  **fail-closed** (`AADS_POLICY_MODE=enforce`, the default); set `audit` to warn
  without blocking.
- **Drift detection (ADR-006)** — `plan_sha256` binds execution to the exact
  approved plan (a TOCTOU guard). On hash mismatch or step failure the executor
  moves the plan to the non-terminal `paused_for_review` status and records an
  `execution_escalations` row; an operator then resumes or aborts from the
  Dashboard.
- **Log-injection defence (ADR-007)** — log data is untrusted input to the LLM. A
  deterministic scanner (`log_guard.py`) fences external log content and taints any
  diagnosis built from suspicious data, forcing human review (a tainted plan can
  never auto-execute).

## Manual Dashboard Demo

Lab/manual demos should keep the Dashboard approval flow visible. The helper
prepares a Nginx bad-config scenario and stops before approval/execution:

```bash
bash scripts/lab/demo-nginx-manual-gate.sh
```

After the Dashboard shows the restore plan, manually click **Approve** and then
**Execute**. Prefer a plan whose runner contains
`aads-nginx-restore-known-good` or operation `nginx.restore_config`; delayed
logs can create stale `nginx.start` items after a previous repair.

Verify a demo run:

```bash
bash scripts/lab/demo-nginx-manual-gate.sh --verify <DIAGNOSIS_ID>
```

In the current lab, `ENABLE_KNOWLEDGE_BASE=false`, so a terminal status of
`kb_skipped` means execution reached the expected post-repair state and skipped
only the knowledge-base import.

## Development Notes

Production-like deployment should use the release installers above. For local
development, use the regular Compose files and deploy helpers documented in
`docs/runbooks/bootstrap.md` and `docs/service-coverage-handoff.md`.

Useful local checks:

```bash
docker compose ps
docker compose logs -f layer2-analyzer
docker compose logs -f layer4-executor
```

## License

MIT License
