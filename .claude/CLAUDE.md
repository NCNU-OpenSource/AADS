# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

AADS (AI Auto Debug System) is a **server + on-device agent** system that collects
service logs, detects anomalies, generates LLM root-cause analysis, and executes
human-approved repair plans through a controlled runner. The repo is a polyglot
monorepo: Python services (one per layer), a Flask + React dashboard, shell-based
installers, and Docker Compose orchestration.

## Architecture (the big picture)

Data flows through numbered layers. Layers 0–3 run on the **server** (Docker
Compose); Layer 4 spans the server (executor) and the **target hosts** (on-device
agent). Understanding the cross-layer contract matters more than any single file.

- **Layer 0 — Collection** (`layer0-collector/`, `layer0-storage/`): Alloy tails
  service logs on targets and pushes to server Loki; `log_archiver.py` writes
  `raw_logs` into TimescaleDB. Postgres schema + migrations live in
  `layer0-storage/timescaledb/migrations/` (numbered `000_…005_…`).
- **Layer 1 — Filter** (`layer1-filter/`): reads Loki, runs a pattern filter +
  LogBERT anomaly detector (`src/filters/`), writes `anomaly_logs`. The
  standalone `ingester/` is an anomaly-ingestion HTTP helper (port 8000).
- **Layer 2 — Analyzer / System Agent** (`layer2-analyzer/`): the brain. A
  LangGraph **ReAct agent** (`src/agent/graph.py`: investigator → tools → planner)
  consumes anomalies, queries Loki/Prometheus via tools (`src/agent/tools.py`),
  calls an LLM through LiteLLM, and emits a structured **FixingPlan** (schemas in
  `src/schemas/`). `src/main.py` (1.3k lines) wires consumers, the notification
  hub, and the HTTP API (port 8080).
- **Layer 3 — Remediation / Gate** (`dashboard/`): Flask backend (`app.py`) +
  React frontend (`static/app/`, JSX served directly). The human-in-the-loop
  approval surface — plan queue, approve/reject/execute, execution trace,
  resume/abort. Admin actions require `AADS_ADMIN_API_KEY` (header
  `X-Admin-API-Key`). Port 5000.
- **Layer 4 — Executor + On-Device Agent**:
  - `layer4-executor/src/executor.py` (Knowledge Agent): polls approved plans
    from TimescaleDB, holds **per-node locks**, executes steps in order, records
    audit/execution state, and routes failures (`classify_step_failure`).
  - `pi-agent/src/main.py` (On-Device Agent): runs on every target, exposes the
    **`runner.v1`** HTTP API (port 8090). Receives per-step **argv-first** runner
    requests, runs them through safety cards + root wrappers (`pi-agent/wrappers/`).

### Security model — read before touching execution paths

The execution path was hardened in three coupled ADRs (see commit
`942bd55` and `docs/SECURITY_HARDENING_AUDIT.md`). These invariants must hold:

- **ExecutionProfile / PolicyCard (ADR-005)**: FixingPlan 3.1 carries a required
  `execution_profile` manifest, generated **deterministically — never by the LLM**
  (`layer2-analyzer/src/runner_catalog.py::execution_profile_for`).
  The `pi-agent` `PolicyCard` (`pi-agent/src/safety_cards/policy_card.py`) enforces
  it **fail-closed** when `AADS_POLICY_MODE=enforce` (the default; `audit` warns but
  still runs). The decision function **`profile_allows()` is duplicated** — in
  `layer2-analyzer/src/schemas/action_plan.py` *and* `pi-agent/src/safety_cards/policy_card.py`
  (not in `runner_catalog.py`, which holds the *generator*). `test_parity.py` pins
  them to **identical verdicts** (behavioural parity, *not* byte-identical source —
  the two already differ in docstrings). Change one body, change the other, or the
  parity test fails. Note `AADS_POLICY_MODE` is **not** in `.env.example` or the
  compose files; it defaults to `enforce` in code only.
- **Drift detection (ADR-006)**: `plan_sha256` binds execution to the exact
  approved plan (TOCTOU guard) and is computed **identically** in `dashboard/app.py`
  and `layer4-executor/src/executor.py` (a third parity coupling — keep them in sync).
  `paused_for_review` is a non-terminal status; `execution_escalations` (migration 005)
  records pauses (`severity` is always `'high'`). Failures route through
  `classify_step_failure` (hook_denied / verification_failed / retries_exhausted
  → pause/escalate, surfaced as resume/abort in the dashboard).
- **Log-injection defence (ADR-007)**: `layer2-analyzer/src/log_guard.py` is a
  deterministic regex scanner + data-fencing + taint registry (contextvars). All
  agent tools that ingest external log data are wrapped; tainted diagnosis forces
  human review. Log data is **untrusted input** to the LLM — keep it fenced.

### Connection model

Targets' Alloy → server Loki. Layer 1 → `anomaly_logs`. Layer 2 → diagnosis
reports + FixingPlans. Dashboard (admin key) → approve/execute. Executor
(`PI_AGENT_TOKEN` bearer) → target `/v1/commands/run`. The on-device agent
advertises `runner_capabilities.schema_version="runner.v1"` from
`/v1/node/facts`. There is intentionally **no static command catalog** as the
safety boundary — runner requests carry argv + context + side-effect metadata,
and safety cards make contextual allow/deny decisions.

## Commands

### Tests

Tests are **per-service** and rely on `sys.path.insert(.., "src")` in each test
file — run pytest **from inside the service directory**. The repo `.venv` has
pytest; lightweight/deterministic tests pass there, but the full Layer 2 suite
needs the heavy agent stack (langchain/langgraph) from
`layer2-analyzer/requirements.txt` installed (otherwise some tests collection-error
or fall back to `conftest.py` stubs).

```bash
# from repo root, against a single service
.venv/bin/python -m pytest pi-agent/tests/ -q          # 35 tests, no extra deps
.venv/bin/python -m pytest layer4-executor/tests/ -q   # 37 tests, no extra deps

# Layer 2 needs the full stack; run from its dir
cd layer2-analyzer && /…/AADS/.venv/bin/python -m pytest tests/ -q

# single test file / single test
cd layer4-executor && python -m pytest tests/test_drift.py -q
cd layer4-executor && python -m pytest tests/test_drift.py::test_plan_sha256 -q
```

Verified collected counts (per service): pi-agent **35**, layer4-executor **37**,
layer1-filter **4**, layer2-analyzer **104** (needs the full agent stack — a degraded
`.venv` shows 1 collection error in `test_agent_tools.py`). The dashboard (Layer 3)
has no tests. Hardcoded totals drift — run `python -m pytest --co -q` from inside a
service dir for the current number rather than trusting a baseline figure.

### Run the stack locally

```bash
cp .env.example .env            # edit secrets (LiteLLM upstream key is the one required value)
docker compose up -d --build    # build images locally
docker compose ps
docker compose logs -f layer2-analyzer
docker compose logs -f layer4-executor
curl http://localhost:8000/health   # Ingester
curl http://localhost:8080/health   # Layer 2 Analyzer
```

`docker-compose.yaml` builds from source; `docker-compose.prod.yaml` pulls
prebuilt `ghcr.io/ncnu-opensource/aads-*:${AADS_IMAGE_TAG}` images and is what the
installers use. DB migrations: `scripts/db/apply-migrations.sh`.

### Deploy (release installers — the recommended path)

Server first (mints the agent token), then agents. See `DEPLOYMENT.md`. Dev
equivalents: `dist/bootstrap-server.sh`, `dist/install-agent.sh`. Images +
release bundles are published by `.github/workflows/build-images.yml` on a
version tag (`git tag vX.Y.Z && git push --tags`).

### Lab / demo verification

```bash
bash scripts/lab/demo-nginx-manual-gate.sh                 # prep + inject fault, STOP before approval
bash scripts/lab/demo-nginx-manual-gate.sh --verify <ID>   # check execution trace + nginx health
bash scripts/lab/e2e-service-repair.sh [SR-PG-01|--service mysql]  # auto approve/execute (not for manual-gate demos)
```

For a manual Dashboard demo, do **not** use `e2e-service-repair.sh` — it
auto-approves via the API. Terminal status `kb_skipped` is a normal success when
`ENABLE_KNOWLEDGE_BASE=false`.

### Pre-commit / CI guard

`scripts/verify-no-spokenly.sh` (pre-commit hook + `.github/workflows/`) asserts
active configs never reference Spokenly. Install hooks with `pre-commit install`.

## Documentation sync (project rule)

同步工具是 **`bash scripts/sync-obsidian`**（一支 bash 腳本，**不是** Python skill；
`~/.claude/skills/` 是空的）。它用 `mc mirror` 或 `aws s3 sync` 把 Obsidian vault
鏡像到 MinIO，由 `AADS_OBSIDIAN_*` 環境變數設定：

- `AADS_OBSIDIAN_VAULT`（預設 `/Volumes/eSSD/obsidian-aads`，外接 SSD 上的 vault）
- `AADS_OBSIDIAN_BUCKET`（預設 `obsidian-aads`）
- `AADS_OBSIDIAN_ENDPOINT`（預設 `https://s3.tfbs.site`）
- `AADS_OBSIDIAN_MC_TARGET`（選用的 `mc` alias）

腳本鏡像 **整個 vault**（排除 `.obsidian/`、`.trash/`、`.DS_Store`、`workspace*.json`、
`data.json`），**沒有副檔名白名單**。先用 `bash scripts/sync-obsidian --dry-run` 預覽。
細節見 [docs/OBSIDIAN_SYNC.md](../docs/OBSIDIAN_SYNC.md)。

- **Endpoint**: https://s3.tfbs.site ｜ **Bucket**: obsidian-aads ｜ **Console**: https://console.tfbs.site

> ⚠️ `AADS_OBSIDIAN_VAULT`（外接 SSD 的 vault）與 repo 內的 `docs/obsidian-vault/`
> 是兩份不同的東西。repo 內目前**只有** `docs/obsidian-vault/ADR/`（ADR-005/006/007，
> 受 git 追蹤但同時被 `.gitignore` 列入）；`Technical-Reports/`、`Research/`、
> `Infrastructure/`、`Services/`、`Database/` **尚未建立**；`Architecture-Overview.canvas`
> 位於 **repo 根目錄**，不在 vault 內。`scripts/sync-obsidian` 同步的是
> `AADS_OBSIDIAN_VAULT` 那一份。

文檔更新原則：重大技術選型／架構／API 決策寫成 ADR（放 `docs/obsidian-vault/ADR/`）；
架構變更同步更新根目錄的 `Architecture-Overview.canvas`；實作或修復完成後更新對應的
`docs/` 文檔。

> The hook scripts exist at `.claude/hooks/` (`post-edit-sync.sh`,
> `stop-drift-check.sh`, `obsidian-sync-check.sh`), but `.claude/settings.json`
> wires them via absolute paths under `/home/bs10081/Developer/ai-auto-debug-system/`
> — a different host than this checkout (`/Users/bs10081/Developer/AADS`). They
> silently no-op here; repoint the paths to this checkout's `.claude/hooks/` if you
> want the sync/drift hooks to fire locally.

## Conventions

- **Python 3.11**, `python:3.11-slim` base. Pydantic v2 (controller services pin
  `2.7.4`; pi-agent uses `>=2.12,<3` — don't unify blindly). LLM access goes
  through the in-stack **LiteLLM proxy** — `graph.py` uses `langchain_openai.ChatOpenAI`
  pointed at `LLM_BASE_URL` (default `http://litellm:4000/v1`), never a provider's
  hosted API directly.
- Service-to-service auth: dashboard/gate admin actions use `AADS_ADMIN_API_KEY`
  (header `X-Admin-API-Key`); server→agent uses `PI_AGENT_TOKEN` bearer (the
  executor falls back to `AADS_AGENT_TOKEN`). Dashboard **GET** endpoints are
  unauthenticated — only mutating POST routes call `require_admin`.
- **FixingPlan schema version is meaningful.** Layer 2 emits **3.1 only** — the
  Pydantic model is `Literal["3.1"]` with a required `execution_profile`
  (`schemas/action_plan.py:302`) and **rejects 3.0**. There is no `validate_plan`
  in layer2; the layer4 executor's `validate_plan` (`executor.py:656`,
  `SUPPORTED_PLAN_SCHEMAS={"3.0","3.1"}`) and the dashboard
  (`SUPPORTED_EXECUTION_SCHEMAS={'3.0','3.1'}`) both accept either for backward
  compat, but 3.0 is **never generated** and is only tolerated at the pi-agent
  boundary under `AADS_POLICY_MODE=audit`.
- **Layer 3 is the `dashboard/` Gate UI**; the notification/suggestion logic runs
  inside `layer2-analyzer` (`_run_layer3()`), not a separate service. The canonical
  DB schema is `layer0-storage/timescaledb/migrations/000…005` (13 tables). The old
  duplicate `layer3-remediation/` and the conflicting legacy `init.sql`/`retention.sql`
  have been removed.
