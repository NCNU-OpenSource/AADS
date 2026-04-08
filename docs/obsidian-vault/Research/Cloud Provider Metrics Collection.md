---
date: 2026-04-08
type: research
topic: metrics-collection-frequency
sources: AWS, Azure, GCP
---

# Cloud Provider Metrics Collection Frequency Research

## Overview

本文件記錄三大雲端廠商（AWS、Azure、GCP）的系統指標採集頻率設定，作為 [[ADR/ADR-001-Metrics-Collection-Frequency|ADR-001]] 的研究依據。

---

## AWS CloudWatch Agent

### 預設配置
| 項目 | 值 |
|------|-----|
| 預設頻率 | **60 秒** |
| 最小頻率 | 1 秒（高解析度） |
| 可選頻率 | 1s / 10s / 30s / 60s |
| 配置位置 | `metrics_collection_interval` |

### 高解析度指標（High-Resolution Metrics）
- CloudWatch 支援 1 秒採集
- **成本考量**：高解析度會增加 API 調用和儲存成本
- **Kubernetes 限制**：Container Insights 的 cadvisor 預設 15 秒，不建議低於此值

### 配置範例
```json
{
  "metrics": {
    "metrics_collection_interval": 60,
    "cpu": {
      "measurement": ["cpu_usage_idle", "cpu_usage_user"],
      "metrics_collection_interval": 10
    }
  }
}
```

### 參考資料
- [CloudWatch Agent Configuration File Details](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Configuration-File-Details.html)
- [Metrics collected by the CloudWatch agent](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/metrics-collected-by-CloudWatch-agent.html)

---

## Azure Monitor Agent

### 預設配置
| 項目 | 值 |
|------|-----|
| 預設頻率 | **10 秒**（Performance Counters） |
| 最小頻率 | 10 秒 |
| 最大頻率 | 1800 秒（30 分鐘） |
| Azure Metrics 限制 | **僅支援 60 秒** |

### 採集與上傳機制
- Agent 緩衝日誌後定期上傳
- 上傳頻率：30 秒 ~ 2 分鐘
- 大部分資料在 1 分鐘內上傳
- Heartbeat 每分鐘一次

### 配置範例
```json
{
  "performanceCounters": {
    "performanceCounterConfiguration": [
      {
        "counterSpecifier": "\\Processor(_Total)\\% Processor Time",
        "sampleRate": "PT60S"
      }
    ]
  }
}
```

### 特殊限制
- 傳送到 Azure Metrics 的資料**只支援 60 秒間隔**
- 傳送到 Log Analytics workspace 則可自由設定

### 參考資料
- [Collect performance counters with Azure Monitor Agent](https://learn.microsoft.com/en-us/azure/azure-monitor/vm/data-collection-performance)
- [Log data ingestion time in Azure Monitor](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/data-ingestion-time)

---

## GCP Cloud Monitoring (Ops Agent)

### 預設配置
| 項目 | 值 |
|------|-----|
| 預設頻率 | **60 秒** |
| 最小頻率 | 10 秒 |
| 配置格式 | duration（如 `30s`, `2m`） |

### 高解析度支援
- 支援 10 秒解析度
- 適用於需要快速告警的關鍵負載
- Legacy collectd agent 也支援 10 秒

### 配置範例
```yaml
metrics:
  receivers:
    hostmetrics:
      type: hostmetrics
      collection_interval: 60s
  service:
    pipelines:
      default_pipeline:
        receivers: [hostmetrics]
```

### 調整建議
- 關鍵負載：減少 collection_interval 以提高告警敏感度
- 一般負載：使用預設 60 秒

### 參考資料
- [Configure the Ops Agent](https://cloud.google.com/monitoring/agent/ops-agent/configuration)
- [Cloud Monitoring metrics get 10-second resolution](https://cloud.google.com/blog/products/management-tools/cloud-monitoring-metrics-get-10-second-resolution)

---

## 三大廠商比較總結

| 廠商 | 預設頻率 | 支援範圍 | 高解析度成本 |
|------|---------|----------|-------------|
| AWS CloudWatch | 60s | 1s ~ 60s | 增加 API/儲存成本 |
| Azure Monitor | 10s (PC) / 60s (Metrics) | 10s ~ 1800s | N/A |
| GCP Cloud Monitoring | 60s | 10s ~ 自訂 | 額外儲存 |

### 共同點
1. **預設都是 60 秒**（或接近）— 這是經過大規模驗證的平衡點
2. **都支援更高頻率** — 但需要考慮成本和資源
3. **高解析度用於關鍵場景** — 需要快速告警的負載

---

## 效能開銷研究

### CPU/Memory 影響
- 現代監控 Agent（如 Netdata）即使**每秒採集**，CPU 開銷也 **< 1%**
- 每分鐘採集的開銷可忽略不計（< 0.1%）

### 常見採集命令開銷
| 命令 | 開銷等級 | 說明 |
|------|---------|------|
| `ps auxww` | 極低 | 讀取 /proc |
| `ss -tulnp` | 極低 | 讀取 socket 資訊 |
| `who` / `w` | 極低 | 讀取 utmp |
| `top -b -n 1` | 低 | 單次快照 |
| `cat /proc/meminfo` | 極低 | 直接讀取 |

### 儲存開銷估算
假設每次採集產生 10KB：

| 頻率 | 每小時 | 每天 | 每月 |
|------|--------|------|------|
| 1 分鐘 | 600 KB | 14.4 MB | **432 MB** |
| 5 分鐘 | 120 KB | 2.9 MB | 86 MB |
| 15 分鐘 | 40 KB | 960 KB | 29 MB |

---

## 結論

基於以上研究：

1. **60 秒是業界標準** — 三大雲端廠商的預設值
2. **每分鐘採集不會造成顯著負載** — CPU < 0.1%
3. **儲存開銷可接受** — 每月約 432 MB，TimescaleDB 可輕鬆處理
4. **異常檢測需要足夠採樣點** — 1 分鐘解析度對 Layer 1 LogBERT 很重要

**建議**：採用 **1 分鐘**採集頻率，與雲端廠商預設一致。

---

## Related
- [[ADR/ADR-001-Metrics-Collection-Frequency]]
- [[Layer 0 - Data Collection]]
- [[Infrastructure/Grafana Alloy]]
