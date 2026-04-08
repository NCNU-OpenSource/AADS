# 🚀 Deployment Guide

## Prerequisites

- Docker & Docker Compose
- NVIDIA GPU (for LogBERT)
- NVIDIA Container Toolkit

## Quick Start

1. **Clone the repository**
   ```bash
   git clone https://github.com/bs10081/ADDS
   cd ADDS
   ```

2. **Validate Alloy configuration**
   ```bash
   docker run --rm -v $(pwd)/layer0-collector/alloy:/config \
     grafana/alloy:latest validate /config/config.alloy
   ```

3. **Start all services**
   ```bash
   docker compose up -d
   ```

4. **Verify health**
   ```bash
   docker compose ps
   curl http://localhost:8000/health  # Ingester
   curl http://localhost:8080/health  # Layer 2 Analyzer
   ```

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
