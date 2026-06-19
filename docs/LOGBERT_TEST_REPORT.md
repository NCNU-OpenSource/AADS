# LogBERT 異常檢測 — 驗證指南

> **重要更正（2026-06）**
> 本檔案先前是一份「測試報告」，記錄的數字（5,364 條異常、異常分數
> 0.738~2.784、各容器分布百分比、7 個 Grafana 面板、5/5 評級等）來自一台
> **外部主機上的獨立 LogBERT 原型**（輸出 JSON 至 `~/Developer/Grafana/logbert/`、
> 再 push 回 Loki，並使用 immich / steam-headless / RTX 5060 Ti 等該主機特有的環境）。
>
> 那份原型與 **本倉庫實際運行的 LogBERT** 並不相同，因此那些數字**無法在本倉庫複現、
> 也無法驗證**，已全部移除。原表格的容器分布百分比本身也是錯的（加總遠超過 100%）。
>
> 本檔案改寫為「如何在本倉庫驗證 Layer 1 LogBERT 整合」的指南。生產路徑的設計細節請見
> [LogBERT 整合指南](./LOGBERT_INTEGRATION.md)。

---

## 本倉庫的 LogBERT 在哪裡

AADS 內**生產用**的 LogBERT 是 **Layer 1 過濾器**的一部分，不是獨立服務：

- 偵測器：`layer1-filter/src/filters/logbert_filter.py`
- 與規則過濾器的融合：`layer1-filter/src/main.py::_run_fusion`
- 異常寫入：`layer1-filter/src/storage/anomaly_store.py`

關鍵事實（以 HEAD 程式碼為準）：

| 項目 | 生產路徑（layer1-filter） | 先前報告的外部原型 |
|------|---------------------------|---------------------|
| 輸出位置 | **TimescaleDB `anomaly_logs` 表**（透過 asyncpg `INSERT`/upsert） | JSON 檔 + push 回 Loki |
| 運算裝置 | **硬編碼 `device='cpu'`**（`main.py:69`） | 視原型而定 |
| torch / transformers / drain3 | `torch==2.1.0`、`transformers==4.35.0`、`drain3==0.9.10`（drain3 已宣告但未 import） | （外部原型，版本未驗證） |
| 依賴缺失時 | **優雅降級**：torch 不存在時 LogBERT 停用，僅跑 pattern filter | — |
| 融合規則 | `pattern_or_logbert`（pattern 與 logbert 任一命中即為異常） | 無此概念 |

> 補充：另有一份獨立的 `logbert/` 原型存放於倉庫中（GPU、pytorch 2.7.0-cuda12.8、
> 輸出 JSON、push 回 Loki），它與 Layer 1 整合**不同**；而本檔案先前描述的外部主機
> 原型又與這兩者**都不一樣**。請以 `layer1-filter/` 為生產真實來源。

---

## `pattern_or_logbert` 融合應該產生什麼

`_run_fusion`（`layer1-filter/src/main.py:230`）的行為：

1. 若啟用，先跑 `PatternFilter`，再跑 `LogBERTFilter`（LogBERT 拋例外時會被捕捉，
   服務改用 pattern 結果繼續運行，不會中斷）。
2. 以 `timestamp|container|message` 為 key 將兩階段結果合併。
3. 對每一筆日誌：
   - **任一**階段判定為異常 → 該筆即為異常（這就是「pattern **or** logbert」）。
   - `anomaly_score` 取兩階段的 **`max()`**。
   - `filter_stage` 為命中階段以 `+` 串接（例如 `pattern`、`logbert`、`pattern+logbert`）。
   - `metadata.fusion_rule == "pattern_or_logbert"`，並在 `metadata.stages` 下保留各階段的
     分數與 metadata。
   - 若兩階段皆未命中，保留一筆正常結果僅供統計使用。

驗證時，這些就是你應該在 `anomaly_logs` 與融合輸出中看到的欄位形狀，而**不是**某個固定的
準確率/異常條數。

---

## 如何驗證（可在本倉庫複現的步驟）

### 1. 跑 Layer 1 的單元測試

Layer 1 目前有 **4 個測試函式 / 2 個檔案**，皆為純邏輯測試，**不需要** torch：

```bash
# 從倉庫根目錄
.venv/bin/python -m pytest layer1-filter/tests/ -q
```

涵蓋：

- `tests/test_pattern_filter.py` — `PatternFilter` 能對 nginx 設定錯誤標記異常、
  對正常訊息不誤報，並回報 `filter_stage == "pattern"`。
- `tests/test_cursor_timestamp.py` — poll cursor 取用最新一筆日誌時間戳、無時間戳時退回
  poll 結束時間。

> 測試數量請以實際收集為準，不要硬記：
> `.venv/bin/python -m pytest layer1-filter/tests/ --co -q`
>
> 注意：這些測試**不會**載入 LogBERT 模型，因此通過它們**不代表**模型推論本身被驗證；
> 它們驗證的是 pattern filter 與輪詢游標邏輯。

### 2. 確認 LogBERT 依賴與降級行為

LogBERT 偵測器在 torch / transformers 缺失時會優雅降級（見 `main.py` 中
`LOGBERT_IMPORT_ERROR` 的處理）。要實際驗證模型推論，需安裝
`layer1-filter/requirements.txt`（torch==2.1.0 等）後，設定 `ENABLE_LOGBERT_FILTER=true`
並提供日誌輸入觀察 `_run_fusion` 的輸出。

### 3. 端到端：確認異常落到 TimescaleDB

生產路徑不寫 JSON、也不 push 回 Loki，異常會 upsert 到 `anomaly_logs`：

```sql
-- 在 TimescaleDB 中
SELECT time, container, filter_stage, anomaly_score,
       filter_metadata->>'fusion_rule' AS fusion_rule
FROM anomaly_logs
ORDER BY time DESC
LIMIT 20;
```

預期 `fusion_rule = pattern_or_logbert`，`filter_stage` 為 `pattern` /
`logbert` / `pattern+logbert` 其中之一。

---

## 不應再引用的內容

- 任何固定的「異常總數 / 準確率 / precision / recall / 異常分數區間」數字 —— 來自外部原型，
  未經本倉庫驗證。
- 「輸出 JSON 到 `output/anomalies.json`」「push 回 Loki」「Grafana 7 面板 Dashboard」——
  屬於外部原型，**非** Layer 1 生產路徑（生產寫 TimescaleDB）。
- 針對 immich / steam-headless / RTX 5060 Ti 的環境特定描述。
- 容器異常分布百分比表（加總破百，數據本身有誤）。

---

## 相關文件

- [LogBERT 整合指南](./LOGBERT_INTEGRATION.md) — 生產路徑與設計細節
- `layer1-filter/src/filters/logbert_filter.py` — 偵測器
- `layer1-filter/src/main.py::_run_fusion` — 融合邏輯
- `layer1-filter/src/storage/anomaly_store.py` — 異常寫入 TimescaleDB

---

**狀態**: 已改寫為驗證指南；先前未經驗證的基準數字已移除。
