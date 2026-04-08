---
date: 2026-04-08
status: accepted
layer: Layer 0 / Layer 1 / Layer 2
component: All
---

# ADR-003: 系統整合修復（從檔案到部署）

## Context（背景）

實作完成後，Codex 發現實際運行的系統與 Plan 不一致：
1. Alloy 配置語法錯誤，無法驗證
2. Ingester 和 Layer 2 webhook 未部署
3. Prometheus 未啟用 remote write receiver
4. TimescaleDB 未使用 migrations

**根本問題**：我們完成了「檔案創建」但沒完成「系統整合」。

## Decision（決策）

執行完整的系統整合修復，確保所有配置與計劃一致。

## Implementation（實作）

### P0: 核心功能修復

#### 1. Alloy WAL 語法修正
```alloy
// 錯誤 ❌
wal {
  enabled = true
  dir     = "/tmp/alloy/wal/loki"
}

// 正確 ✅
wal {
  enabled = true
}
```

**其他語法修正**：
- `env("HOSTNAME")` → `constants.hostname`
- `concat(a, b)` → `a + b`
- 移除 `prometheus.remote_write` 的 WAL 區塊（預設啟用）

#### 2. Prometheus Remote Write
```yaml
command:
  - '--web.enable-remote-write-receiver'
```

#### 3. Ingester 服務
```yaml
ingester:
  build: ./layer1-filter/ingester
  ports: ["8000:8000"]
  environment:
    DB_HOST: timescaledb
```

#### 4. Layer 2 Webhook
```python
@app.post("/api/webhooks/anomaly")
async def receive_anomaly_webhook(payload: AnomalyWebhookPayload):
    background_tasks.add_task(analyzer.process_anomaly_batch, [anomaly])
    return {"status": "accepted"}
```

#### 5. TimescaleDB Migrations
```yaml
volumes:
  - ./layer0-storage/timescaledb/migrations:/docker-entrypoint-initdb.d:ro
```

### P1: 完整功能

#### 6. 掛載 Log 與 Proc
```yaml
volumes:
  - /var/log:/var/log:ro
  - /proc:/host/proc:ro
```

```alloy
prometheus.exporter.process "all_processes" {
  procfs_path = "/host/proc"
  matcher {
    name    = "{{.Comm}}"
    cmdline = [".+"]
  }
}
```

#### 7. 測試依賴
```txt
pytest-asyncio==0.23.4
```

## Verification（驗證）

```bash
✅ grafana/alloy:latest validate config.alloy
✅ docker compose config（無語法錯誤）
✅ 所有服務已配置
```

## Consequences（後果）

### 好處
- 配置語法正確，可以部署
- Fan-out 路徑完整（Ingester + LLM Webhook）
- Metrics 可以正確傳送
- TimescaleDB 自動壓縮和清理生效

### 代價
- 無

## Lessons Learned

**問題根源**：實作時只關注「寫檔案」，忽略了「整合」。

**改進方向**：
1. 每完成一個組件就驗證語法
2. 使用 docker compose config 檢查整合
3. 部署前完整測試

## Related
- [[ADR/ADR-001-Metrics-Collection-Frequency]]
- [[ADR/ADR-002-Event-Driven-Log-Collection]]
- [[Layer 0 - Data Collection]]
- [[Layer 1 - Anomaly Filtering]]
- [[Layer 2 - Root Cause Analysis]]
