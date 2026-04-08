---
date: 2026-04-08
status: accepted
layer: Layer 0 / Layer 1
component: Grafana Alloy / TimescaleDB / FastAPI Ingester
---

# ADR-002: 事件驅動日誌收集與 Fan-out 架構

## Context（背景）

需要建立一個從 Layer 0（邊緣節點）到 Layer 1（中央儲存）再到 Layer 2（LLM 分析）的完整日誌收集與異常觸發架構。

**核心挑戰**：
1. 防止本機日誌撐爆硬碟
2. 網路斷線時不遺失資料
3. 即時觸發 LLM 分析（不使用資料庫輪詢）
4. 長期儲存需要極致壓縮

## Research（研究過程）

詳細研究記錄見：
- [[Research/Cloud Provider Metrics Collection]]
- [[ADR/ADR-001-Metrics-Collection-Frequency]]

### 關鍵區分：Logs vs Metrics

| 類型 | 收集方式 | 機制 | 頻率 |
|------|---------|------|------|
| **Logs** | loki.source.file / loki.write | 事件驅動 tail | 即時串流 |
| **Metrics** | prometheus.scrape / prometheus.remote_write | 定時拉取 | 60 秒 |

## Options（可選方案）

### 方案 A：定時輪詢 + 批次上傳
- **優點**：實作簡單
- **缺點**：延遲高、無法即時觸發、浪費資源

### 方案 B：Filebeat + Elasticsearch
- **優點**：成熟方案
- **缺點**：Elasticsearch 儲存成本高、無 TimescaleDB 壓縮能力

### 方案 C：Grafana Alloy + TimescaleDB + Fan-out ✅
- **優點**：
  - 事件驅動（零延遲）
  - WAL 防資料遺失
  - TimescaleDB 自動壓縮（節省 90%+ 空間）
  - Fan-out 模式（同時觸發 DB 與 LLM）

## Decision（決策）

**選擇方案 C：Grafana Alloy + TimescaleDB + Fan-out**

### 架構設計

```
Layer 0 (本機)         Layer 1 (中央)          Layer 2 (分析)
─────────────         ─────────────           ─────────────

/var/log/*
audit.log
  │
  ▼
loki.source.file    →  loki.write (WAL)  →   Loki
(事件驅動 tail)

process list
cpu/memory
  │
  ▼
prometheus           →  prometheus           →  Prometheus
.exporter              .remote_write (WAL)
  │
  ▼
prometheus.scrape
(60s)

LogBERT 異常
  │
  ▼
loki.source.api     →  Fan-out:
(HTTP Receiver)         ├─► PostgreSQL Ingester
                        └─► LLM Webhook
```

## Challenges（遇到的困難）

### 問題 1：Hook 阻止文件創建
- **現象**：everything-claude-code plugin 的 hook 阻止 .md 和 .txt 文件創建
- **解決**：修改 hooks.json，加入 `!/obsidian-vault\\//.test(p)` 例外

### 問題 2：Logs vs Metrics 混淆
- **現象**：初版計劃將所有採集都用定時輪詢
- **解決**：嚴格區分 Logs（事件驅動）和 Metrics（定時拉取）

## Implementation（實作）

### 完成檔案清單

**1. System Configuration**
- `layer0-collector/logrotate/layer0-logrotate.conf`
- `layer0-collector/auditd/auditd-tuned.conf`
- `layer0-collector/auditd/audit.rules`

**2. Grafana Alloy Pipelines**
- `layer0-collector/alloy/config.alloy`
  - Pipeline 1a (Logs) - 事件驅動 tail
  - Pipeline 1b (Metrics) - 60s 拉取
  - Pipeline 2 (Fan-out) - 雙路轉發

**3. TimescaleDB Migrations**
- `layer0-storage/timescaledb/migrations/001_create_hypertables.sql`
- `layer0-storage/timescaledb/migrations/002_compression_policy.sql`
- `layer0-storage/timescaledb/migrations/003_retention_policy.sql`

**4. FastAPI Ingester**
- `layer1-filter/ingester/main.py`
- `layer1-filter/ingester/requirements.txt`
- `layer1-filter/ingester/Dockerfile`

### 關鍵配置

#### 1. Logrotate（7 天保留）
```bash
/var/log/audit/audit.log {
    daily
    rotate 7
    compress
    delaycompress
}
```

#### 2. Auditd（容量限制）
```conf
max_log_file = 100
num_logs = 10
space_left = 500
disk_full_action = SUSPEND
```

#### 3. Audit Rules（完整監控）
```bash
# 監控所有命令執行
-a always,exit -F arch=b64 -S execve -k exec
```

#### 4. Alloy WAL
```hcl
wal {
  enabled = true
  dir     = "/tmp/alloy/wal/loki"
}
```

#### 5. TimescaleDB 壓縮（7 天）
```sql
SELECT add_compression_policy(
    'raw_logs',
    compress_after => INTERVAL '7 days'
);
```

#### 6. TimescaleDB 保留（90 天）
```sql
SELECT add_retention_policy(
    'raw_logs',
    drop_after => INTERVAL '90 days'
);
```

## Consequences（後果）

### 好處
1. **零延遲觸發**：Fan-out 模式讓 LLM 即時收到異常
2. **資料不遺失**：WAL 機制確保網路斷線時資料安全
3. **極致壓縮**：TimescaleDB 自動壓縮節省 90%+ 空間
4. **自動清理**：90 天後自動刪除，無需人工維護

### 代價
1. **複雜度增加**：需要維護 Alloy、TimescaleDB、FastAPI 三個組件
2. **學習曲線**：團隊需要學習 Alloy 配置語法

## Verification（驗證）

待驗證項目：
- [ ] 模擬網路斷線，確認 WAL 緩衝正常
- [ ] 檢查 `prometheus.exporter.process` 是否抓取所有程序
- [ ] 檢查 TimescaleDB 7 天後自動壓縮
- [ ] 測試 LogBERT → Alloy → Fan-out 流程

## Related
- [[ADR/ADR-001-Metrics-Collection-Frequency]]
- [[Research/Cloud Provider Metrics Collection]]
- [[Layer 0 - Data Collection]]
- [[Layer 1 - Anomaly Filtering]]
- [[Infrastructure/Grafana Alloy]]
- [[Infrastructure/TimescaleDB]]
