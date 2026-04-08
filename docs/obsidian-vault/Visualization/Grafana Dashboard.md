---
title: Grafana Dashboard
type: service
layer: visualization
tags: [visualization, grafana, monitoring]
port: 3000
created: 2026-04-08
---

# Grafana Dashboard

## Overview

Grafana 提供完整的可觀測性儀表板，整合日誌、指標和告警功能。

## Role in System

- 日誌查詢和探索（[[Loki]] 數據源）
- 指標監控（[[Prometheus]] 數據源）
- 告警規則配置
- 統一的可視化平台

## Configuration

**位置:** `grafana/provisioning/`

### Datasources

`datasources/datasources.yaml`:
```yaml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true

  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100

  - name: TimescaleDB
    type: postgres
    url: timescaledb:5432
    database: autodebug
    user: postgres
    secureJsonData:
      password: ${POSTGRES_PASSWORD}
```

### Dashboard Provisioning

`dashboards/dashboards.yaml`:
```yaml
apiVersion: 1

providers:
  - name: 'default'
    orgId: 1
    folder: 'AI Auto Debug'
    type: file
    disableDeletion: false
    editable: true
    options:
      path: /etc/grafana/provisioning/dashboards
```

## Docker Compose

```yaml
grafana:
  image: grafana/grafana:10.0.0
  ports:
    - "3000:3000"
  environment:
    GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    GF_INSTALL_PLUGINS: grafana-clock-panel
  volumes:
    - ./grafana/provisioning:/etc/grafana/provisioning
    - grafana-data:/var/lib/grafana
  depends_on:
    - prometheus
    - loki
```

## Pre-built Dashboards

### System Overview
- 容器 CPU/Memory 使用率
- GPU 使用率（如有）
- 網路 I/O

### Anomaly Dashboard
- 異常發生趨勢
- 嚴重程度分布
- 容器異常熱力圖

### Log Explorer
- 即時日誌查看
- LogQL 查詢介面
- 日誌標籤過濾

## Panel Examples

### Container CPU Usage
```promql
sum(rate(container_cpu_usage_seconds_total{name!=""}[5m])) by (name) * 100
```

### Anomaly Count
```logql
sum(count_over_time({container=~".+"} |= "error" [1h])) by (container)
```

### Memory Usage
```promql
container_memory_working_set_bytes{name!=""} / 1024 / 1024
```

## Access

- **URL:** `http://localhost:3000`
- **Default User:** admin
- **Default Password:** (from `GRAFANA_PASSWORD` env)

## Data Sources

| Name | Type | URL |
|------|------|-----|
| Prometheus | prometheus | http://prometheus:9090 |
| Loki | loki | http://loki:3100 |
| TimescaleDB | postgres | timescaledb:5432 |

## Related

- [[Prometheus]] - 指標數據源
- [[Loki]] - 日誌數據源
- [[TimescaleDB]] - SQL 數據源
- [[Dashboard]] - 簡化版 Web UI

## References

- [Grafana Documentation](https://grafana.com/docs/grafana/latest/)
- [Grafana Dashboards](https://grafana.com/grafana/dashboards/)
