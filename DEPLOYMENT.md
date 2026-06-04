# 🚀 Deployment Guide

AADS has two sides: a **server** (Docker Compose stack) and one or more
**on-device agents** (installed on the machines you want auto-debugged).
Deploy the server first — it mints the token the agents need.

## One-Click Deployment (recommended)

### 1. Server (run on the controller host)

**HTTPS — pull directly from GitHub (no repo checkout needed):**

```bash
curl -fsSL https://raw.githubusercontent.com/bs10081/AADS/main/dist/install-server.sh | bash
```

Downloads `aads-server.tgz` from GitHub Releases, extracts it to `~/aads`
(or `/opt/aads` when run as root), then runs the interactive bootstrap.
Fully non-interactive if all vars are pre-set:

```bash
curl -fsSL https://raw.githubusercontent.com/bs10081/AADS/main/dist/install-server.sh | \
  AADS_LITELLM_UPSTREAM_API_KEY=sk-... bash
```

**From a repo checkout (local / dev):**

```bash
bash dist/bootstrap-server.sh
```

Both paths interactively collect the upstream LLM API key (the only required
value), auto-generate all other secrets, pull the prebuilt images
(`ghcr.io/bs10081/aads-*`), start the stack, verify the database, and print
the exact agent install command — with the token already filled in.

> Private registry? Authenticate once before running:
> `echo $GHCR_TOKEN | docker login ghcr.io -u <github-user> --password-stdin`

### 2. On-Device Agent (run on each target)

The server bootstrap prints both variants with the token already filled in.
Pick whichever fits your network:

**HTTPS (recommended — pulls from public GitHub, no server port needed):**

```bash
curl -fsSL https://raw.githubusercontent.com/bs10081/AADS/main/dist/install-agent.sh | sudo \
  AADS_SERVER=<server-ip> \
  AADS_AGENT_TOKEN=<token> \
  AADS_ADMIN_API_KEY=<admin-key> \
  AADS_RELEASE_BASE_URL=https://github.com/bs10081/AADS/releases/latest/download bash
```

**HTTP / air-gapped (payload served by the AADS dashboard itself):**

```bash
curl -fsSL http://<server>:5000/install-agent.sh | sudo \
  AADS_SERVER=<server> \
  AADS_AGENT_TOKEN=<token> \
  AADS_ADMIN_API_KEY=<admin-key> bash
```

Both commands install the On-Device Agent (V2 runner), auto-register the node,
and install the Alloy log forwarder. Every prompt can be pre-set via environment
variable (see the header of `dist/install-agent.sh`).

### 3. Verify

```bash
# server
docker compose -f docker-compose.prod.yaml --env-file .env ps
curl -H "X-Admin-API-Key: <admin-key>" http://<server>:5000/api/agents

# target
curl -H "Authorization: Bearer <token>" http://<target>:8090/v1/node/facts
systemctl is-active aads-agent alloy
```

## Building / publishing images

Images are built and pushed by `.github/workflows/build-images.yml` on a
version tag (`git tag v1.0.0 && git push --tags`). It also attaches
`aads-agent.tgz` + `install-agent.sh` to the GitHub release, so agents can
alternatively pull from the release by setting `AADS_RELEASE_BASE_URL`.

## Manual / Dev Start (build locally)

```bash
git clone https://github.com/bs10081/AADS
cd AADS
cp .env.example .env   # edit secrets
docker compose up -d --build   # builds images locally instead of pulling
docker compose ps
curl http://localhost:8000/health  # Ingester
curl http://localhost:8080/health  # Layer 2 Analyzer
```

## Prerequisites

- Docker & Docker Compose v2
- (Optional) NVIDIA GPU + Container Toolkit for the GPU LogBERT profile

## Architecture

```
Layer 0 (Collection) → Layer 1 (Filter) → Layer 2 (Analysis) → Layer 3 (Remediation)
         ↓                    ↓                   ↓
      Loki              PostgreSQL          Notifications
      Prometheus        TimescaleDB
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| Loki | 3100 | Log aggregation |
| Prometheus | 9090 | Metrics storage |
| Grafana | 3000 | Visualization |
| TimescaleDB | 5432 | Time-series database |
| Dashboard | 5000 | Web UI |
| Ingester | 8000 | Anomaly ingester |
| Layer2 Analyzer | 8080 | LLM analysis webhook |
| Alloy | 12345 | Log/metrics collector |

## Event-Driven Flow

```
LogBERT detects anomaly
    ↓
POST to Alloy :9999
    ↓
Fan-out to:
  ├─► Ingester :8000  (DB write)
  └─► Layer2 :8080    (LLM analysis)
```

## Troubleshooting

- **Alloy validation fails**: Check syntax with `grafana/alloy:latest validate`
- **Prometheus 404 on remote write**: Ensure `--web.enable-remote-write-receiver` is set
- **No metrics from processes**: Check `/proc` volume mount

## Documentation

See `docs/obsidian-vault/` for complete architecture documentation.
