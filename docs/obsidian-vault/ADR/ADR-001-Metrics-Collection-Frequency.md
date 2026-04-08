---
date: 2026-04-08
status: accepted
layer: Layer 0
component: Grafana Alloy / System State Collector
---

# ADR-001: 系統狀態採集頻率選擇

## Context（背景）

Layer 0 需要定時採集系統狀態資訊，包括：
- Process list（進程列表）
- 線上使用者
- 網路連線
- CPU/Memory 使用率

需要決定採集頻率，在監控精度和系統負載之間取得平衡。

## Research（研究過程）

詳細研究記錄見：[[Research/Cloud Provider Metrics Collection]]

### 調查內容
1. 三大雲端廠商（AWS、Azure、GCP）的預設採集頻率
2. 每分鐘採集對系統效能的影響
3. 儲存開銷估算

### 關鍵發現
- AWS CloudWatch 預設：60 秒
- Azure Monitor 預設：10-60 秒
- GCP Cloud Monitoring 預設：60 秒
- 每分鐘採集的 CPU 開銷：< 0.1%

## Options（可選方案）

### 方案 A：每 15 分鐘
- **優點**：最低儲存開銷（29 MB/月）
- **缺點**：解析度太低，可能漏掉短暫異常

### 方案 B：每 5 分鐘
- **優點**：低儲存開銷（86 MB/月）
- **缺點**：仍可能漏掉快速發生的問題

### 方案 C：每 1 分鐘 ✅
- **優點**：
  - 與業界標準一致（AWS/GCP 預設）
  - 足夠的解析度捕捉異常
  - 對 LogBERT 提供足夠採樣點
- **缺點**：
  - 較高儲存開銷（432 MB/月）
  - 但對 TimescaleDB 來說仍可輕鬆處理

### 方案 D：每 30 秒
- **優點**：更高解析度
- **缺點**：超出業界標準，額外開銷不值得

## Decision（決策）

**選擇方案 C：每 1 分鐘採集**

理由：
1. 與三大雲端廠商預設一致，經過大規模驗證
2. CPU 開銷可忽略（< 0.1%）
3. 儲存開銷可接受，TimescaleDB 可處理
4. 提供足夠的採樣點給 Layer 1 LogBERT 進行異常檢測

## Implementation（實作）

### 系統狀態採集器
```yaml
# 每分鐘執行
collection_interval: 60s

metrics:
  - process_list    # ps auxww
  - online_users    # who, w
  - network_connections  # ss -tulnp
  - cpu_memory      # /proc/meminfo, /proc/loadavg
```

### 混合策略（可選優化）
| 指標類型 | 頻率 | 原因 |
|---------|------|------|
| Process list | 1 分鐘 | 捕捉短暫異常進程 |
| 線上使用者 | 1 分鐘 | 追蹤登入活動 |
| 網路連線 | 1 分鐘 | 偵測異常連線 |
| 磁碟使用率 | 5 分鐘 | 變化較慢 |
| 配置變更 | 即時 | 使用 auditd |

## Consequences（後果）

### 好處
- 提供足夠的監控精度
- 與業界標準一致，易於對比
- 不會對系統造成顯著負載

### 代價
- 每月約 432 MB 額外儲存
- 需要配置 TimescaleDB 的資料保留策略

## Related
- [[Research/Cloud Provider Metrics Collection]]
- [[Layer 0 - Data Collection]]
- [[Infrastructure/Grafana Alloy]]
- [[Infrastructure/TimescaleDB]]
