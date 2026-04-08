---
title: Log Archiver
type: technology
layer: 0
tags: [infrastructure, sync, storage]
created: 2026-04-08
---

# Log Archiver

## Overview

Log Archiver 是一個自定義的 Python 服務，負責將 [[Loki]] 中的日誌同步到 [[TimescaleDB]] 進行永久存儲。

## Role in System

- 定期從 Loki 查詢日誌
- 寫入 TimescaleDB `raw_logs` 表
- 確保日誌不會因 Loki 保留期限而丟失

## Source Code

**位置:** `layer0-storage/sync/log_archiver.py`

```python
import asyncio
import aiohttp
import asyncpg
from datetime import datetime, timedelta

class LogArchiver:
    def __init__(self):
        self.loki_url = os.environ.get("LOKI_URL", "http://loki:3100")
        self.poll_interval = int(os.environ.get("ARCHIVE_INTERVAL", "300"))
    
    async def fetch_logs(self, start: datetime, end: datetime):
        """Fetch logs from Loki"""
        query = '{job=~".+"}'
        url = f"{self.loki_url}/loki/api/v1/query_range"
        params = {
            "query": query,
            "start": int(start.timestamp() * 1e9),
            "end": int(end.timestamp() * 1e9),
            "limit": 5000
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                return await resp.json()
    
    async def store_logs(self, logs: list):
        """Store logs in TimescaleDB"""
        conn = await asyncpg.connect(...)
        await conn.executemany('''
            INSERT INTO raw_logs (timestamp, container, service, level, message, labels)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT DO NOTHING
        ''', logs)
    
    async def run(self):
        """Main loop"""
        while True:
            end = datetime.utcnow()
            start = end - timedelta(seconds=self.poll_interval)
            logs = await self.fetch_logs(start, end)
            await self.store_logs(logs)
            await asyncio.sleep(self.poll_interval)
```

## Docker Compose

```yaml
log-archiver:
  build:
    context: ./layer0-storage/sync
  environment:
    LOKI_URL: http://loki:3100
    DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@timescaledb:5432/autodebug
    ARCHIVE_INTERVAL: "300"
  depends_on:
    - loki
    - timescaledb
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `LOKI_URL` | `http://loki:3100` | Loki API URL |
| `DATABASE_URL` | - | PostgreSQL 連接字串 |
| `ARCHIVE_INTERVAL` | `300` | 同步間隔（秒） |

## Data Flow

```mermaid
graph LR
    A[Loki] -->|query_range| B[Log Archiver]
    B -->|INSERT| C[TimescaleDB]
    C --> D[raw_logs table]
```

## Related

- [[Loki]] - 日誌來源
- [[TimescaleDB]] - 存儲目標
- [[Layer 0 - Data Collection]]
