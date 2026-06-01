# Day-0 Bootstrap Runbook

This runbook brings up the v4.2 AADS lab topology:

- macOS admin workstation using OrbStack for local container debugging.
- Ubuntu controller VM running the AADS Docker Compose stack, Gate, System Agent, and Knowledge Agent.
- Ubuntu target VM running nginx, telemetry, and On-Device Agent.

Production-like runtime belongs on Ubuntu. macOS is only the admin/dev surface.

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
5. Install nginx, root-owned wrappers, sudoers, pi-agent, and the systemd unit on the target.
6. Install target Alloy log forwarding to push `/var/log/nginx/error.log` into controller Loki.
7. Create the first known-good nginx snapshot under `/var/lib/aads-agent/snapshots/nginx/`.
8. Start On-Device Agent and verify `/health` fails fast if wrappers, sudoers, or snapshot are unsafe.
9. Install Docker and Compose on the controller VM, then set Docker daemon DNS to `AADS_DOCKER_DNS_PRIMARY` and `AADS_DOCKER_DNS_SECONDARY` (defaults: `1.1.1.1`, `8.8.8.8`). Multipass NAT DNS can resolve from the VM while failing inside Docker build containers.
10. Start the controller compose stack.
11. Register the target with the controller using the `node_id`, environment, `agent_version`, base URL, and supported commands returned by `/v1/node/facts`.
12. Run baseline health and no-anomaly tests before injecting broken states.

`restore_known_good_config` must return `blocked: no_snapshot` if the snapshot does not exist.

## Agent Execution Model

- `System Agent` stores executable `FixingPlan` JSON with `schema_version="2.0"`.
- `Gate` approves, rejects, or queues execution through `POST /api/plans/{plan_id}/execute`.
- `Knowledge Agent` runs on the controller and calls the target On-Device Agent one catalog step at a time. It does not push the whole plan to the target.
- `On-Device Agent` remains stateless for execution. It exposes `/health`, `/v1/node/facts`, `/v1/probes/run`, `/v1/actions/run`, and the reserved `/v1/agent-tasks/run` RCA dispatch contract.
- Verification and final verification must be catalog probes with structured results. Free text and LLM judgement are not valid execution checks.

## Commands

```bash
scripts/lab/up.sh
scripts/lab/sync-controller.sh
scripts/lab/e2e-repair.sh
scripts/lab/down.sh
```

If Docker builds fail with `Temporary failure in name resolution` during `pip install`, rerun `scripts/lab/up.sh` or set:

```bash
AADS_DOCKER_DNS_PRIMARY=1.1.1.1 AADS_DOCKER_DNS_SECONDARY=8.8.8.8 scripts/lab/up.sh
```

Use `scripts/lab/sync-controller.sh` to copy the current workstation checkout to `aads-controller:~/AADS`. The script disables macOS AppleDouble resource-fork files and deletes any `._*` files on the controller so Grafana provisioning does not try to parse files like `._dashboards.yaml`.

`scripts/lab/e2e-repair.sh` resets the target nginx config from the known-good snapshot before each injected scenario. This keeps interrupted previous E2E attempts from contaminating the next baseline.

## Idempotency And Expiry

- Plan approvals expire after 30 minutes.
- Expiry is checked only when execution starts; in-flight commands rely on command timeout.
- `POST /execute` requires `Idempotency-Key`.
- Same key + same plan returns the original execution.
- Same key + different plan returns `400`.
- Same plan + different key within the 30-minute idempotency TTL returns `409`.
- AgentTask dispatch follows the same idempotency TTL pattern.

## Rollback And Recovery

- Mutating FixingPlans call `nginx.ensure_known_good_snapshot` before the first step.
- v1 snapshot scope is nginx config only: `/etc/nginx/nginx.conf` and `/etc/nginx/sites-enabled`.
- Any aborted step with snapshot enabled triggers `nginx.restore_known_good_config`.
- Controller restart recovery is DB-driven: verified steps are skipped, a running step is verified first, and uncertain state becomes `execution_failed_unknown_state`.

## macOS Development

- Use OrbStack for local image build, image inspection, and single-container debugging.
- Use Multipass for full compose stack testing: one controller VM and one target VM.
- Do not rely on macOS Docker behavior for production parity.
- GPU/DCGM is optional in lab; start DCGM only with the `gpu` compose profile on hosts with NVIDIA runtime. The default Layer 1 container runs without NVIDIA device reservations so Multipass CPU-only labs can start.

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
