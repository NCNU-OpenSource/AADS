---
title: Layer 1 Filter
type: service
layer: 1
tags: [service, anomaly-detection, filtering]
created: 2026-04-08
---

# Layer 1 Filter

## Overview

Layer 1 Filter 是異常過濾層的核心服務，負責從 [[Loki]] 拉取日誌，使用 [[LogBERT]] 進行異常檢測，並將檢測到的異常存儲到 [[TimescaleDB]]。

## Role in System

- 從 Loki 定期拉取日誌
- 執行多層過濾管道
- 使用 LogBERT 計算異常分數
- 將異常寫入 `anomaly_logs` 表

## Architecture

```mermaid
graph LR
    A[Loki] -->|pull logs| B[Layer 1 Filter]
    B --> C[Filter Pipeline]
    C --> D[LogBERT Filter]
    D --> E[Anomaly Store]
    E --> F[PostgreSQL]
```

## Source Code

**位置:** `layer1-filter/src/`

### Main Entry

`main.py`:
```python
import asyncio
from pipeline import FilterPipeline
from storage.anomaly_store import AnomalyStore

async def main():
    pipeline = FilterPipeline()
    store = AnomalyStore()
    
    while True:
        # Fetch logs from Loki
        logs = await fetch_logs_from_loki()
        
        # Run through filter pipeline
        anomalies = await pipeline.process(logs)
        
        # Store anomalies
        for anomaly in anomalies:
            await store.save(anomaly)
        
        await asyncio.sleep(POLL_INTERVAL)
```

### Filter Pipeline

`pipeline.py`:
```python
from filters.logbert_filter import LogBERTFilter

class FilterPipeline:
    def __init__(self):
        self.filters = [
            LogBERTFilter(threshold=0.5)
        ]
    
    async def process(self, logs: list) -> list:
        anomalies = []
        for log in logs:
            for filter in self.filters:
                score = await filter.score(log)
                if score > filter.threshold:
                    anomalies.append({
                        "log": log,
                        "score": score,
                        "filter": filter.name
                    })
        return anomalies
```

### Filter Interface

`filters/base.py`:
```python
from abc import ABC, abstractmethod

class BaseFilter(ABC):
    def __init__(self, threshold: float):
        self.threshold = threshold
    
    @abstractmethod
    async def score(self, log: dict) -> float:
        """Return anomaly score between 0 and 1"""
        pass
```

## Configuration

**配置文件:** `layer1-filter/config/filters.yaml`

```yaml
filters:
  - name: logbert
    enabled: true
    threshold: 0.5
    batch_size: 50

  - name: random_forest
    enabled: false
    threshold: 0.7

polling:
  interval: 10  # seconds
  lookback: 60  # seconds

loki:
  url: http://loki:3100
  query: '{job=~".+"}'
```

## Docker Compose

```yaml
layer1-filter:
  build:
    context: ./layer1-filter
  environment:
    LOKI_URL: http://loki:3100
    DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@timescaledb:5432/autodebug
    ANOMALY_THRESHOLD: "0.5"
    POLL_INTERVAL: "10"
    BATCH_SIZE: "50"
  depends_on:
    - loki
    - timescaledb
    - logbert
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOKI_URL` | `http://loki:3100` | Loki API URL |
| `DATABASE_URL` | - | PostgreSQL 連接字串 |
| `ANOMALY_THRESHOLD` | `0.5` | 異常閾值 (0-1) |
| `POLL_INTERVAL` | `10` | 輪詢間隔（秒） |
| `BATCH_SIZE` | `50` | 批次處理大小 |

## Output Schema

寫入 `anomaly_logs` 表：

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL | 主鍵 |
| `timestamp` | TIMESTAMPTZ | 異常發現時間 |
| `container` | TEXT | 容器名稱 |
| `log_message` | TEXT | 原始日誌訊息 |
| `anomaly_score` | FLOAT | 異常分數 (0-1) |
| `processed` | BOOLEAN | 是否已被 Layer 2 處理 |

## Related

- [[LogBERT]] - 異常檢測引擎
- [[Loki]] - 日誌來源
- [[TimescaleDB]] - 異常存儲
- [[Layer 2 - Root Cause Analysis]] - 下游處理
