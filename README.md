# AI 自動 Debug 系統

> 完整的多層 AI 驅動自動除錯系統 - 從日誌收集到根因分析再到修復建議

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python: 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://python.org)

## 🎯 快速導航

1. [系統概述](#系統概述)
2. [架構設計](#架構設計)
3. [快速開始](#快速開始)
4. [配置說明](#配置說明)
5. [使用指南](#使用指南)
6. [開發文檔](#開發文檔)

---

## 系統概述

完整的四層 AI 自動除錯系統，參考 [Metoro.io](https://metoro.io) 的產品理念，整合日誌收集、異常檢測、根因分析和修復建議。

### 核心功能

- ✅ **Layer 0**: RAW log 永久存儲（TimescaleDB）
- ✅ **Layer 1**: 多層異常過濾（LogBERT + 預留 RF）
- ✅ **Layer 2**: AI 根因分析（LLM + RAG）
- ✅ **Layer 3**: 修復建議與通知（Slack）

### 技術棧

| 類別 | 技術 |
|------|------|
| 日誌收集 | Grafana Alloy, Loki |
| 指標監控 | Prometheus, cAdvisor, DCGM Exporter |
| 時序資料庫 | TimescaleDB |
| AI/ML | BERT (LogBERT), OpenAI API, Ollama |
| 向量資料庫 | ChromaDB |
| 可視化 | Grafana |
| 容器化 | Docker Compose |

---

## 架構設計

```
┌───────────────────────────────────────────────────────────────┐
│                    四層 AI Auto-Debug 架構                     │
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  Layer 0: 數據收集與永久存儲                                   │
│  ├─ Docker Logs → Alloy → Loki (31天) → TimescaleDB (永久)   │
│  └─ Prometheus (CPU/Memory/GPU Metrics)                      │
│                                                               │
│  Layer 1: 多層異常過濾                                        │
│  ├─ RFFilter (預留接口)                                       │
│  ├─ LogBERTFilter (BERT-based, 125 logs/sec GPU)            │
│  └─ PostgreSQL (異常 log 永久保存)                           │
│                                                               │
│  Layer 2: 根因分析                                           │
│  ├─ 異常聚類 (時間窗口 + 模式識別)                             │
│  ├─ Metrics 關聯 (Prometheus PromQL)                         │
│  ├─ 知識庫檢索 (ChromaDB + RAG)                              │
│  └─ LLM 推理 (OpenAI API / Ollama)                          │
│                                                               │
│  Layer 3: 修復建議與通知                                      │
│  ├─ 建議生成 (規則 + LLM)                                     │
│  └─ Slack/Webhook 通知                                       │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

### 資料流

```
[Docker Logs] → [Alloy] → [Loki raw] → [LogBERT] → [Anomaly DB]
                              ↓                           ↓
                        [TimescaleDB]            [Layer 2 Analyzer]
                         (永久保存)                     ↓
                                               [Diagnosis Report]
                                                      ↓
                                            [Slack Notification]
```

---

## 快速開始

### 前置需求

- Docker 20.10+
- Docker Compose 2.0+
- NVIDIA GPU（可選，用於 LogBERT 加速）
- LLM API Key（OpenAI 或第三方相容 API）

### 1. 克隆專案

```bash
git clone <repository-url>
cd ai-auto-debug-system
```

### 2. 配置環境變數

```bash
cp .env.example .env
```

編輯 `.env` 填入必要配置：

```bash
# 資料庫密碼
TIMESCALEDB_PASSWORD=your_secure_password

# LLM 配置（支援自定義 baseURL）
LLM_BASE_URL=https://api.openai.com/v1  # 或 NEW API 等第三方
LLM_API_KEY=sk-xxxxx
LLM_MODEL=gpt-4o-mini

# Slack 通知
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxxxx
```

### 3. 啟動服務

```bash
docker compose up -d
```

### 4. 驗證部署

```bash
# 檢查服務狀態
docker compose ps

# 查看 Layer 2 日誌
docker compose logs -f layer2-analyzer

# 訪問 Grafana
open http://localhost:3000  # 預設帳密: admin/admin
```

---

## 配置說明

### 環境變數

| 變數 | 說明 | 預設值 |
|------|------|--------|
| `TIMESCALEDB_PASSWORD` | TimescaleDB 密碼 | - |
| `LLM_BASE_URL` | LLM API baseURL（支援自定義） | `https://api.openai.com/v1` |
| `LLM_API_KEY` | LLM API Key | - |
| `LLM_MODEL` | LLM 模型名稱 | `gpt-4o-mini` |
| `LLM_STRATEGY` | LLM 策略 | `cascade` |
| `SLACK_WEBHOOK_URL` | Slack Webhook URL | - |
| `SEVERITY_FILTER` | 通知嚴重程度過濾 | `medium` |
| `AUTO_REMEDIATION_ENABLED` | 自動修復（安全起見預設關閉） | `false` |

### LLM 策略

- `cascade`: 先用 Ollama 快速分類，再用 OpenAI 深度分析
- `primary_only`: 僅使用 OpenAI API
- `local_only`: 僅使用 Ollama

### 第三方 API 支援

系統支援任何 OpenAI 相容 API，設定 `LLM_BASE_URL` 即可：

```bash
# NEW API
LLM_BASE_URL=https://api.newapi.com/v1

# Azure OpenAI
LLM_BASE_URL=https://your-resource.openai.azure.com/openai/deployments/your-deployment

# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
```

---

## 使用指南

### 訪問 Grafana Dashboard

1. 開啟瀏覽器訪問 http://localhost:3000
2. 登入（admin/admin）
3. 查看以下 Dashboard：
   - **System Monitoring**: 系統資源監控
   - **LogBERT Anomaly**: 異常日誌
   - **Diagnosis Reports**: AI 診斷報告（新）

### 查詢診斷報告

連接 TimescaleDB：

```bash
docker exec -it timescaledb psql -U logdb -d logdb
```

```sql
-- 最近 10 筆診斷
SELECT diagnosis_id, timestamp, severity, summary
FROM diagnosis_reports
ORDER BY timestamp DESC
LIMIT 10;

-- 高嚴重程度診斷
SELECT * FROM diagnosis_reports
WHERE severity IN ('high', 'critical')
ORDER BY timestamp DESC;
```

### Slack 通知範例

當檢測到異常時，系統會自動發送 Slack 通知：

```
🔴 AI Debug Alert - HIGH

Summary: steam-headless 容器 NVIDIA 驅動下載失敗

Root Cause:
- Category: dependency_failure
- Confidence: 85.0%
- Description: 無法從 GitHub 下載 NVIDIA 驅動程式

Recommended Actions:
1. 檢查 NVIDIA_DRIVER_VERSION 環境變數設定
2. 重新啟動容器讓其重新嘗試下載

Diagnosis ID: diag_cluster_0_1712345678
```

---

## 開發文檔

### 目錄結構

```
ai-auto-debug-system/
├── layer0-collector/       # 日誌收集
├── layer0-storage/         # 持久化存儲
├── layer1-filter/          # 多層過濾
├── layer2-analyzer/        # 根因分析 + 通知
├── layer3-remediation/     # 修復建議（參考）
├── logbert/                # LogBERT 系統
├── grafana/                # Grafana 配置
└── prometheus/             # Prometheus 配置
```

### 資料庫 Schema

**raw_logs** (TimescaleDB Hypertable)
```sql
CREATE TABLE raw_logs (
    time TIMESTAMPTZ NOT NULL,
    container TEXT,
    message TEXT,
    labels JSONB,
    PRIMARY KEY (time, container)
);
```

**anomaly_logs**
```sql
CREATE TABLE anomaly_logs (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ,
    container TEXT,
    raw_message TEXT,
    template TEXT,
    anomaly_score FLOAT,
    filter_stage TEXT,
    is_confirmed BOOLEAN
);
```

**diagnosis_reports**
```sql
CREATE TABLE diagnosis_reports (
    id SERIAL PRIMARY KEY,
    diagnosis_id TEXT UNIQUE,
    timestamp TIMESTAMPTZ,
    severity TEXT,
    summary TEXT,
    root_cause JSONB,
    recommended_actions JSONB
);
```

### 擴展 Layer 1 Filter

實作新的過濾器：

```python
# layer1-filter/src/filters/custom_filter.py
from filters.base import BaseFilter, FilterResult

class CustomFilter(BaseFilter):
    @property
    def filter_name(self) -> str:
        return "custom"

    def predict(self, logs: List[Dict]) -> List[FilterResult]:
        # 實作過濾邏輯
        pass
```

在 `filters.yaml` 中啟用：

```yaml
filters:
  - name: custom
    enabled: true
    threshold: 0.5
```

---

## 效能指標

| 指標 | 數值 |
|------|------|
| 端到端延遲 | < 90 秒 |
| LogBERT 吞吐量 (GPU) | 125 logs/sec |
| LogBERT 吞吐量 (CPU) | 14.3 logs/sec |
| Layer 2 分析延遲 | < 40 秒 |
| RAW log 保留 | 永久（TimescaleDB） |
| 異常 log 保留 | 永久（PostgreSQL） |

---

## 疑難排解

### LogBERT 無法啟動（GPU）

```bash
# 檢查 NVIDIA runtime
docker info | grep -i runtime

# 檢查 GPU 可見性
docker run --rm --gpus all nvidia/cuda:12.0-base nvidia-smi
```

### TimescaleDB 連接失敗

```bash
# 檢查服務健康狀態
docker compose ps timescaledb

# 查看日誌
docker compose logs timescaledb

# 測試連接
docker exec -it timescaledb psql -U logdb -d logdb
```

### Layer 2 無法取得 LLM 回應

```bash
# 檢查環境變數
docker compose exec layer2-analyzer env | grep LLM

# 測試 API 連接
curl -X POST "${LLM_BASE_URL}/chat/completions" \
  -H "Authorization: Bearer ${LLM_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"test"}]}'
```

---

## 授權

MIT License

---

## 貢獻

歡迎提交 Issue 和 Pull Request！

---

## 參考資料

- [Metoro.io](https://metoro.io) - 產品靈感來源
- [LogBERT](https://github.com/logpai/logbert) - 異常檢測演算法
- [TimescaleDB](https://www.timescale.com/) - 時序資料庫
- [ChromaDB](https://www.trychroma.com/) - 向量資料庫
- [Grafana](https://grafana.com/) - 可觀測性平台

---

**建立時間**: 2026-04-07  
**版本**: 1.0.0  
**作者**: Claude Opus 4.6 + RegChien
