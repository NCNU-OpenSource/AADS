# LogBERT 日誌異常檢測整合指南（Layer 1）

## 概述

LogBERT 是 AADS **Layer 1 — Filter** 中以 BERT 為基礎的語意異常偵測器。它與
確定性的 pattern filter **融合**運作，由 `layer1-filter` 服務持續從 Loki 拉取
新日誌、進行雙路偵測，並把命中的異常寫入 TimescaleDB 的 `anomaly_logs` 資料表，
供 Layer 2（Analyzer）後續取用。

> 本文件描述的是**產品內 Layer 1 的 LogBERT**（`layer1-filter/src/filters/logbert_filter.py`）。
> repo 內另有一個**獨立的 `logbert/` 原型**（GPU、Loki push、JSON 輸出），與 Layer 1
> 產品**不同**——請見文末「附錄：獨立 `logbert/` 原型」一節，不要混用。

### 核心功能

- **輪詢偵測**：每 `POLL_INTERVAL` 秒（預設 10 秒）從 Loki 查詢自上次游標後的新日誌
  （`layer1-filter/src/main.py:38`、`process_batch`）。
- **雙路融合**：`pattern` filter（高召回 regex）與 `logbert`（BERT MLM 語意分數）
  同時跑，再由 `_run_fusion` 以 `pattern_or_logbert` 規則合併
  （`main.py:230`，metadata `fusion_rule="pattern_or_logbert"`）。
- **AI 分析**：LogBERT 使用 BERT Masked Language Model，以遮罩預測損失（loss）
  作為異常分數，分數越高代表偏離正常模式越多。
- **持久化**：偵測到的異常透過 `AnomalyStore` 批次寫入 **TimescaleDB `anomaly_logs`**
  資料表（**不是** JSON 檔，**也不是** push 回 Loki）。
- **優雅降級**：若執行環境缺少 `torch`/`transformers`，LogBERT 會在 import 階段
  被略過，服務僅以 pattern filter 繼續運作（`main.py:19-25, 65-72`）。

---

## 架構說明

```
Targets' Alloy ──► Server Loki
                      │
                      ▼
            layer1-filter 服務 (無對外 host port)
              ├─ fetch_logs_from_loki()  : 以 LOKI_QUERY 查 query_range
              ├─ PatternFilter.predict() : 確定性 regex（高召回，穩定 lab 場景）
              ├─ LogBERTFilter.predict() : BERT MLM 語意分數（可選，需 torch）
              ├─ _run_fusion()           : pattern_or_logbert 融合
              └─ AnomalyStore.store_batch(): 寫入 TimescaleDB anomaly_logs
                      │
                      ▼
            Layer 2 (Analyzer) 消費 anomaly_logs
```

融合規則：對同一筆日誌（key = `timestamp|container|message`），只要任一路判定為
異常即標記異常；`anomaly_score` 取兩路最大值，`filter_stage` 以 `+` 串接觸發的階段
（例如 `pattern+logbert`），`metadata.fusion_rule` 固定為 `pattern_or_logbert`
（`main.py:241-277`）。

---

## 部署資訊

`layer1-filter` 由 `docker-compose.yaml` 從原始碼建置（`build: ./layer1-filter`），
**不對外暴露任何 host port**——它是純背景 worker，輸出寫進 TimescaleDB。

### 服務配置

| 項目 | 值 |
|------|-----|
| **Container 名稱** | `layer1-filter`（`docker-compose.yaml:169`） |
| **基礎映像** | `python:3.11-slim`（`layer1-filter/Dockerfile`） |
| **運算模式** | **CPU**，於 `main.py:69` 以 `device='cpu'` 硬編碼建立 LogBERTFilter |
| **輪詢間隔** | `POLL_INTERVAL` 秒（compose 預設 10） |
| **異常閾值** | `ANOMALY_THRESHOLD`（預設 0.5；對 LogBERT 是 MLM loss 閾值） |
| **輸出位置** | TimescaleDB `anomaly_logs` 資料表 |
| **對外 port** | 無（背景服務） |

### 環境變數（以 `layer1-filter/src/main.py` 為準）

| 變數 | 預設值 | 說明 |
|------|--------|------|
| `LOKI_URL` | `http://loki:3100` | Loki 基底 URL |
| `LOKI_TENANT` | `raw` | 查詢時帶的 `X-Scope-OrgID` |
| `LOKI_QUERY` | `{source="target-nginx"}`（compose 由 `LAYER1_LOKI_QUERY` 注入） | LogQL stream selector |
| `POLL_INTERVAL` | `10` | 兩次輪詢間隔（秒） |
| `INITIAL_LOOKBACK_SECONDS` | `300` | 首次啟動（無游標時）向前回看的秒數 |
| `ANOMALY_THRESHOLD` | `0.5` | 異常分數閾值（LogBERT = MLM loss） |
| `BATCH_SIZE` | `50` | 單次 Loki query 的 `limit` |
| `ENABLE_PATTERN_FILTER` | `true` | 是否啟用 pattern filter |
| `ENABLE_LOGBERT_FILTER` | `true` | 是否啟用 LogBERT（缺 torch 時自動略過並告警） |
| `AADS_NODE_ID` / `NODE_ID` | `controller` | 寫入 `anomaly_logs` 的 node 標識 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_USER` / `DB_PASSWORD` | `timescaledb` / `5432` / `logdb` / `logdb` / `logdb_password` | TimescaleDB 連線 |

> 注意：LogBERT 的模型路徑在 `main.py:67` 固定為 HuggingFace 的 `bert-base-uncased`；
> compose 會把 `./logbert/models` 掛到 `/app/models` 以重用模型檔
> （`docker-compose.yaml:193`），但 Layer 1 預設仍以 `bert-base-uncased` 名稱載入。

### 相依套件（`layer1-filter/requirements.txt`）

| 套件 | 版本 |
|------|------|
| `torch` | `2.1.0` |
| `transformers` | `4.35.0` |
| `numpy` | `<2.0` |
| `drain3` | `0.9.10`（已宣告，但目前 `layer1-filter/src` **未實際 import**） |
| `asyncpg` | `0.29.0`（寫入 TimescaleDB） |
| `aiohttp` | `3.9.3`（查詢 Loki） |

---

## 使用方式

### 啟動服務（隨整個 stack）

```bash
docker compose up -d --build layer1-filter
docker compose logs -f layer1-filter
```

啟動時服務會列出實際生效的設定（`main.py:207-215`），可用來確認 query、閾值、
輪詢間隔與兩路 filter 是否啟用。

### 檢查偵測結果（查 TimescaleDB，而非 JSON）

```bash
# 最近 10 筆異常
docker compose exec timescaledb \
  psql -U logdb -d logdb -c \
  "SELECT time, node_id, service, filter_stage, anomaly_score, left(raw_message, 80) AS msg
     FROM anomaly_logs ORDER BY time DESC LIMIT 10;"

# 只看 LogBERT（或含 LogBERT 的融合）命中的異常
docker compose exec timescaledb \
  psql -U logdb -d logdb -c \
  "SELECT time, filter_stage, anomaly_score FROM anomaly_logs
     WHERE filter_stage LIKE '%logbert%' ORDER BY time DESC LIMIT 20;"

# 高分數異常
docker compose exec timescaledb \
  psql -U logdb -d logdb -c \
  "SELECT time, filter_stage, anomaly_score FROM anomaly_logs
     WHERE anomaly_score > 0.7 ORDER BY anomaly_score DESC LIMIT 20;"
```

`anomaly_logs` 採 `dedup_key`（node + service + template + 分鐘桶）去重，同一分鐘內
重複的異常會累加 `occurrence_count` 而非重複插入（`anomaly_store.py:140-173`）。

---

## 調整配置

在 `docker-compose.yaml` 的 `layer1-filter.environment` 或 `.env` 調整：

```yaml
layer1-filter:
  environment:
    - POLL_INTERVAL=30                  # 放慢輪詢以降低 CPU 負載
    - ANOMALY_THRESHOLD=0.3            # 更敏感（更多異常）
    # 或 ANOMALY_THRESHOLD=2.0         # 更保守（LogBERT loss 偏高才算異常）
    - ENABLE_LOGBERT_FILTER=false      # 只跑 pattern filter（不需 torch）
    - ENABLE_PATTERN_FILTER=true
    - LOKI_QUERY={source="target-nginx"}
```

調整後重啟：

```bash
docker compose up -d layer1-filter
```

> 提示：`ANOMALY_THRESHOLD` 同時套用到 pattern 與 LogBERT，但語意不同。pattern filter
> 的 `is_anomaly` 由是否命中 regex 決定（命中時分數為 1.0）；LogBERT 則是
> `loss > threshold`。要調 LogBERT 靈敏度時，記得 0.5 是 BERT loss 尺度（典型 0.5–2.0），
> 不是 0–1 的機率。

---

## 異常檢測原理

### Pattern filter（確定性，高召回）

`PatternFilter`（`layer1-filter/src/filters/pattern_filter.py`）以一組大小寫不敏感
的 regex 比對訊息，命中任一個即標記異常（分數 1.0）。內建樣式涵蓋
`failed to start`、`nginx: [emerg]`、`connection refused`、`out of memory`、
`critical`/`fatal`/`panic`、以及泛用的 `error|failed|failure`，用來確保已知的
lab/nginx 故障狀態一定會被surfaced。

### LogBERT（語意，BERT MLM）

`LogBERTFilter`（`layer1-filter/src/filters/logbert_filter.py`）流程：

1. 以 `window_size`（預設 10）將日誌切成滑動視窗，少於 3 筆的視窗略過。
2. 取每筆日誌的 `template`（無則退回 `message`），用 ` [SEP] ` 串成序列。
3. Tokenize（`max_length=512`、truncation），隨機遮罩約 15% 的 token。
4. 以 `BertForMaskedLM` 計算遮罩預測的 **loss**，作為該視窗的異常分數。
5. `loss > threshold` → 視窗內所有日誌標記為異常。

### 融合（`_run_fusion`，`main.py:230`）

兩路結果依 `timestamp|container|message` 聚合：任一路判異常即為異常，分數取最大、
階段以 `+` 串接，`fusion_rule="pattern_or_logbert"`。沒有任何一路觸發的日誌會保留
一筆正常結果以維持統計，但不會寫入 `anomaly_logs`（只有 `is_anomaly=True` 會落庫）。

---

## 優雅降級

LogBERT 的相依（`torch`/`transformers`）在實驗或精簡環境可能缺席。此時：

- import 失敗會被攔截，`LogBERTFilter` 設為 `None`，並印出
  `LogBERT disabled because dependencies are unavailable: ...`
  （`main.py:19-25, 71-72`）。
- 服務繼續以 pattern filter 運作，不會崩潰。
- 即使 LogBERT 已載入，若單次 `predict` 丟例外，`_run_fusion` 也會記錄錯誤並
  退回 pattern 結果（`main.py:235-239`），不會中斷批次。

要在開發環境完全關閉 LogBERT，設 `ENABLE_LOGBERT_FILTER=false` 即可，無須安裝 torch。

---

## 故障排除

### LogBERT 沒有命中任何異常

```bash
# 1) 確認 LogBERT 真的有啟用（而非因缺 torch 被略過）
docker compose logs layer1-filter | grep -i "LogBERT"

# 2) 閾值是否太高（LogBERT 是 loss 尺度）→ 降低 ANOMALY_THRESHOLD
# 3) Loki 是否有符合 LOKI_QUERY 的資料
docker compose logs layer1-filter | grep -i "Processing"
```

### 服務啟動但只跑 pattern

通常是 `torch`/`transformers` 未安裝或 import 失敗。檢查啟動日誌的
`LogBERT disabled because dependencies are unavailable` 訊息，並確認映像有依
`requirements.txt` 安裝 `torch==2.1.0`、`transformers==4.35.0`。

### CPU 使用率偏高

Layer 1 的 LogBERT 以 `device='cpu'` 執行（`main.py:69`）。可：

- 提高 `POLL_INTERVAL` 降低批次頻率；
- 縮小 `LOKI_QUERY` 範圍或降低 `BATCH_SIZE`；
- 在不需要語意偵測的環境設 `ENABLE_LOGBERT_FILTER=false`，只保留 pattern filter。

---

## 測試

Layer 1 的測試從服務目錄內執行（見專案 `CLAUDE.md`）：

```bash
.venv/bin/python -m pytest layer1-filter/tests/ -q
```

要確認當下實際收集到的測試數量，請執行：

```bash
.venv/bin/python -m pytest layer1-filter/tests/ --co -q
```

（不要假設固定數字——以 `--co` 的輸出為準。）

---

## 附錄：獨立 `logbert/` 原型（與 Layer 1 不同）

repo 根目錄的 `logbert/` 是一個**獨立的研究原型**，**不是** Layer 1 產品，也**未**被
`docker-compose.yaml` 引用。請勿把它的設定套到 `layer1-filter`。兩者差異：

| 面向 | Layer 1（`layer1-filter/`，產品） | 獨立 `logbert/`（原型） |
|------|-----------------------------------|--------------------------|
| 基礎映像 | `python:3.11-slim` | `pytorch/pytorch:2.7.0-cuda12.8-cudnn9-runtime`（GPU） |
| 運算裝置 | `device='cpu'` 硬編碼 | 走 PyTorch CUDA |
| torch / transformers | `2.1.0` / `4.35.0`（釘版） | `torch>=2.0.0` / `transformers>=4.30.0` |
| 輸出去向 | 寫入 TimescaleDB `anomaly_logs` | 寫 `output/anomalies.json` **並** push 回 Loki（`push_anomalies_to_loki`） |
| 觸發方式 | `POLL_INTERVAL` 輪詢 Loki query_range | Loki 輪詢/串流 client（`loki_poll_client.py` 等） |
| Compose | 由主 `docker-compose.yaml` 建置、無對外 port | 自帶 `logbert/docker-compose.yaml`，需 `nvidia` runtime 與外部 `grafana_observability` 網路 |
| 角色 | 生產偵測器（融合 pattern filter） | 實驗/評估原型，獨立運行 |

> 另外，repo 裡 `docs/LOGBERT_TEST_REPORT.md` 與 `docs/logbert_performance_analysis.txt`
> 描述的是更早、跑在外部主機的第三版原型，與上述兩者皆不完全對應，請以本文件與原始碼為準。

---

## 參考資源

- `layer1-filter/src/main.py`（融合與輪詢主迴圈）
- `layer1-filter/src/filters/logbert_filter.py`（BERT MLM 偵測器）
- `layer1-filter/src/filters/pattern_filter.py`（確定性 regex 偵測器）
- `layer1-filter/src/storage/anomaly_store.py`（寫入 `anomaly_logs`）
- [LogBERT 論文](https://arxiv.org/abs/2103.04475)
- [BERT 模型 (Hugging Face)](https://huggingface.co/bert-base-uncased)

---

最後更新：2026-06-17
