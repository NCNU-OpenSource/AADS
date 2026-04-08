---
title: API Endpoints
type: reference
tags: [api, endpoints, reference]
created: 2026-04-08
---

# API Endpoints

## Dashboard API

**Base URL:** `http://localhost:5000`

| Endpoint | Method | Parameters | Description |
|----------|--------|------------|-------------|
| `/` | GET | - | 主頁面 |
| `/api/diagnosis` | GET | `hours` (default: 24) | 診斷報告列表 |
| `/api/stats` | GET | `hours` (default: 24) | 統計數據 |

### Example: Get Diagnoses

```bash
curl "http://localhost:5000/api/diagnosis?hours=24"
```

```json
[
  {
    "id": "diag_abc123",
    "created_at": "2026-04-08T10:30:00Z",
    "severity": "high",
    "root_cause": "Database connection pool exhausted",
    "suggestions": ["Restart service", "Increase pool size"]
  }
]
```

---

## Loki API

**Base URL:** `http://localhost:3100`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/loki/api/v1/push` | POST | 推送日誌 |
| `/loki/api/v1/query` | GET | 即時查詢 |
| `/loki/api/v1/query_range` | GET | 範圍查詢 |
| `/loki/api/v1/labels` | GET | 獲取標籤 |
| `/loki/api/v1/tail` | WebSocket | 尾部追蹤 |

### Example: Query Logs

```bash
curl -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={container="api-server"} |= "error"' \
  --data-urlencode 'start=1712534400000000000' \
  --data-urlencode 'end=1712620800000000000'
```

---

## Prometheus API

**Base URL:** `http://localhost:9090`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/query` | GET/POST | 即時查詢 |
| `/api/v1/query_range` | GET/POST | 範圍查詢 |
| `/api/v1/labels` | GET | 標籤列表 |
| `/api/v1/targets` | GET | 抓取目標 |

### Example: Query Metrics

```bash
curl "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=container_cpu_usage_seconds_total{name!=""}'
```

---

## Ollama API

**Base URL:** `http://localhost:11434`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/generate` | POST | 生成完成 |
| `/api/chat` | POST | 對話完成 |
| `/api/pull` | POST | 拉取模型 |
| `/api/tags` | GET | 列出模型 |

### Example: Generate

```bash
curl "http://localhost:11434/api/generate" \
  -d '{"model": "llama2", "prompt": "Why did the database crash?", "stream": false}'
```

---

## Grafana API

**Base URL:** `http://localhost:3000`

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/dashboards` | GET | 列出儀表板 |
| `/api/datasources` | GET | 列出數據源 |
| `/api/search` | GET | 搜索 |

### Authentication

```bash
curl -u admin:${GRAFANA_PASSWORD} "http://localhost:3000/api/dashboards"
```

---

## Related

- [[Dashboard]]
- [[Loki]]
- [[Prometheus]]
- [[Ollama]]
- [[Grafana Dashboard]]
