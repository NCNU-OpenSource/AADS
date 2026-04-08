---
title: Configuration Files
type: reference
tags: [configuration, reference]
created: 2026-04-08
---

# Configuration Files

## Main Configuration

| File | Purpose |
|------|---------|
| `docker-compose.yaml` | Docker 服務定義 |
| `.env` | 環境變數（從 .env.example 複製） |

---

## Layer 0

| File | Purpose |
|------|---------|
| `layer0-collector/alloy/config.alloy` | Alloy 日誌收集配置 |
| `layer0-collector/loki/loki-config.yaml` | Loki 配置 |
| `layer0-storage/timescaledb/init.sql` | 資料庫初始化 |
| `layer0-storage/timescaledb/retention.sql` | 資料保留策略 |
| `prometheus/prometheus.yaml` | Prometheus 抓取配置 |

---

## Layer 1

| File | Purpose |
|------|---------|
| `layer1-filter/config/filters.yaml` | 過濾器配置 |

### filters.yaml

```yaml
filters:
  - name: logbert
    enabled: true
    threshold: 0.5
    batch_size: 50

polling:
  interval: 10
  lookback: 60

loki:
  url: http://loki:3100
  query: '{job=~".+"}'
```

---

## Layer 2

| File | Purpose |
|------|---------|
| `layer2-analyzer/config/config.yaml` | 分析器配置 |

### config.yaml

```yaml
consumer:
  batch_size: 100
  poll_interval: 30

aggregator:
  time_window: 300
  similarity_threshold: 0.8

llm:
  strategy: cascade  # cascade, primary_only, local_only
  openai:
    model: gpt-4-turbo-preview
    max_tokens: 2000
  ollama:
    model: llama2
    url: http://ollama:11434

knowledge_base:
  persist_dir: /app/chroma_db
  top_k: 3
```

---

## Layer 3

| File | Purpose |
|------|---------|
| `layer3-remediation/config/notifications.yaml` | 通知配置 |

### notifications.yaml

```yaml
notifications:
  min_severity: medium

  slack:
    enabled: true
    webhook_url: ${SLACK_WEBHOOK_URL}

  webhook:
    enabled: false
    url: ${WEBHOOK_URL}

auto_remediation:
  enabled: false
  max_restarts_per_hour: 3
  allowed_operations:
    - restart
    - health_check
```

---

## Visualization

| File | Purpose |
|------|---------|
| `grafana/provisioning/datasources/datasources.yaml` | Grafana 數據源 |
| `grafana/provisioning/dashboards/dashboards.yaml` | Grafana 儀表板 |

---

## Environment Variables

### .env.example

```bash
# Database
POSTGRES_PASSWORD=your_password_here

# LLM
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4-turbo-preview
OLLAMA_MODEL=llama2

# Notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/...
WEBHOOK_URL=https://your-webhook.com/...

# Grafana
GRAFANA_PASSWORD=admin_password

# Auto Remediation (default: disabled)
AUTO_REMEDIATION_ENABLED=false
```

---

## Related

- [[Docker Compose Services]]
- [[Layer 0 - Data Collection]]
- [[Layer 1 - Anomaly Filtering]]
- [[Layer 2 - Root Cause Analysis]]
- [[Layer 3 - Remediation]]
