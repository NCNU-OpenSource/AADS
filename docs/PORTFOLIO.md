# AADS — AI Auto Debug System

> **觀測 → 診斷 → 修復** 的全自動化伺服器維運閉環系統
> 以 LLM 進行根因分析、產生修復計畫，經人工審批閘道後，由確定性執行器在目標主機上安全地完成修復。

| | |
|---|---|
| **專案類型** | 分散式系統 / AIOps / LLM Agent 應用 |
| **規模** | 約 10,000+ 行 Python、14+ 個容器化服務、雙節點（Controller + Target）架構 |
| **技術棧** | Python (FastAPI, Flask, LangChain/LangGraph)、Docker Compose、TimescaleDB、Grafana Loki/Prometheus、LiteLLM、systemd |
| **授權** | MIT License |

---

## 1. 問題與動機

伺服器服務（nginx、PostgreSQL、Redis、MySQL）故障時，傳統流程需要工程師手動翻日誌、判斷根因、下指令修復——耗時且依賴經驗。LLM 雖然擅長從日誌推理根因，但**直接讓 LLM 在主機上執行指令是極度危險的**（幻覺指令、提示注入、不可回復的破壞）。

AADS 要解決的核心問題是：**如何讓 LLM 的推理能力進入維運流程，同時把「執行」完全關進確定性的安全邊界內。**

核心設計哲學：**「寧可停下，不可亂動」**——每一個邊界情況（無快照、過期授權、並行衝突、上游故障）都傾向安全地阻擋，而不是在不確定狀態下執行修改。

---

## 2. 系統架構：Three-Agent + Gate

```
┌─────────────┐   logs   ┌──────────────┐  anomalies  ┌───────────────┐
│ On-Device   │─────────▶│  Layer 1     │────────────▶│  System Agent │
│ Agent       │  (Alloy) │  Filter      │             │  (Layer 2)    │
│ (目標主機)   │          │  異常過濾     │             │  LLM 根因分析  │
└─────────────┘          └──────────────┘             └───────┬───────┘
      ▲                                                       │ FixingPlan 3.0
      │ runner API                                            ▼
      │ (/v1/commands/run)                           ┌─────────────────┐
      │                                              │  Gate Console   │
      │                                              │  人工審批閘道    │
      │                                              └────────┬────────┘
      │                                                       │ approve + execute
      │                                              ┌────────▼────────┐
      └───────────────────────────────────────────── │ Knowledge Agent │
              per-step 執行修復                        │  確定性執行器    │
                                                     └─────────────────┘
```

| 元件 | 技術 | 職責 |
|------|------|------|
| **Layer 0 收集** | Grafana Alloy + Loki + TimescaleDB | 目標主機日誌（nginx/PG/Redis/MySQL/syslog）即時轉發、短期查詢與長期歸檔 |
| **Layer 1 過濾** | Python + Drain3 + LogBERT (PyTorch/Transformers) | 從海量日誌中過濾出異常事件，支援 pattern filter 與深度學習異常偵測雙路徑 |
| **Layer 2 System Agent** | LangGraph Agent + LiteLLM | 消費異常、聚合 cluster（Map-Reduce）、查詢 Loki/Prometheus 佐證、LLM 產生根因分析與 `FixingPlan 3.0` |
| **Gate** | Flask + React 18 | 人工審批閘道：approve / reject / execute、30 分鐘授權窗口、idempotency 保護、執行軌跡視覺化 |
| **Layer 4 Executor** | Python（**零 LLM 依賴**，單元測試保證） | DB 驅動的確定性狀態機：node lock → 快照 → 逐步執行 → 驗證 → 稽核 |
| **On-Device Agent** | FastAPI + systemd | 目標主機上的 argv-first Command Runner，`shell=False`、單一 root wrapper、append-only audit |

資料層使用 **TimescaleDB**（PostgreSQL 16 hypertable），12 張核心表貫穿全流程：raw logs、anomalies、diagnosis reports、approvals、executions、node locks、audit events、agent registry、knowledge cases。

---

## 3. 一次完整修復的生命週期

1. 目標主機的 Alloy 將服務日誌推送到 Controller 的 Loki。
2. Layer 1 輪詢 Loki，將異常寫入 `anomaly_logs`。
3. Layer 2 每 30 秒消費異常、聚合成 cluster，透過 LiteLLM 呼叫 LLM 做根因分析。
4. LLM 產出展示用計畫後，**確定性後處理**將其轉為可執行的 `FixingPlan 3.0`（Pydantic 嚴格驗證）。
5. Dashboard Queue 出現待審批計畫；Operator 輸入 Admin Key 進行 **Approve**。
6. Operator 按 **Execute**（帶 Idempotency-Key），Gate 建立 queued execution。
7. Executor 取得 node lock，先建立 **known-good 快照**，再逐步呼叫目標主機的 runner API。
8. 每一步都有宣告式驗證（如 `systemctl is-active` → `{active=true}`、HTTP 200 檢查），失敗依策略 rollback 或 abort。
9. final verification 通過後寫入終態，全程記錄於 `audit_events`。

已驗證的端到端修復場景：**Nginx 壞設定、PostgreSQL 停機、Redis 壞設定、MySQL 壞設定**——全部由系統自動偵測、診斷，經人工審批後自動修復成功。

---

## 4. 技術亮點

### 4.1 LLM 防注入的分層設計

這是本專案最核心的安全工程：**LLM 永遠不能直接產生 shell 指令**。

- LLM 的輸出只能是 `(service, operation)` 語意對（如 `nginx.restore_config`）。
- Layer 2 的 `runner_catalog` 以確定性程式碼將其翻譯成固定的 argv runner spec（含 read/mutate 分類、as_root、timeout）。
- `affected_service` 只能來自觀測標籤（Loki label），不能來自 LLM 自由文字。
- 所有日誌一律視為「資料」而非指令，杜絕日誌內容的提示注入。
- 目標主機的 agent 只認 argv、以 `subprocess.run(shell=False)` 執行，不存在任意 shell 通道。

### 4.2 確定性執行器與故障韌性

Layer 4 Executor 完全不含 LLM——以混沌測試（Chaos E2E）驗證的韌性機制：

| 機制 | 防範的故障 |
|------|-----------|
| Node lock（TTL + 啟動時 sweep） | 並行修復互相干擾、lock 洩漏 |
| 強制 pre-execution snapshot | 在沒有回滾基準的狀態下修改設定 |
| DB-driven 狀態恢復 | Executor crash 後重複執行 |
| 30 分鐘授權窗口 | 過期審批被利用 |
| Idempotency key 矩陣 | 重複提交執行請求 |
| 上游故障靜默降級 | Loki/Prometheus 斷線時誤判誤修 |

### 4.3 最小特權的目標主機代理

- 特權操作只透過**單一** root-owned runner wrapper，sudoers 只授權這一支。
- 每次執行經過 `before_run → execute → after_run` 的 Hook pipeline，內建 AuditHook 產生 append-only 稽核紀錄，架構預留 safety card 的 contextual deny 能力。
- `side_effect=mutate` 才觸發 node lock 與快照，read probe 不受影響。

### 4.4 雙路徑異常偵測

- **Pattern filter**：Drain3 日誌模板比對，低延遲、可解釋。
- **LogBERT**（可選）：以 Transformer 學習日誌序列的正常模式，偵測未知型異常。

### 4.5 一鍵部署與可重現性

- `install-server.sh` / `install-agent.sh` 釋出版安裝器：自動下載 bundle、產生 secrets、註冊節點。
- GHCR 容器映像 + release tag pin，支援 air-gapped 安裝（Dashboard 可直接服務 agent 安裝檔）。
- Multipass 本機 lab 一鍵建立（`scripts/lab/up.sh`），含自動化 Chaos E2E 與 Service-Repair E2E 測試腳本。

---

## 5. 可觀測性

完整的 Grafana + Prometheus + cAdvisor（+ GPU 環境的 DCGM Exporter）觀測棧：

- 系統監控 dashboard（容器資源、服務健康）
- Loki 日誌查詢與 Layer 1 過濾效果追蹤
- Dashboard 內建 execution trace：每個修復步驟的指令、輸出、驗證結果可逐步檢視

---

## 6. 工程實務

- **測試**：pytest（runner / executor / Layer 2 單元測試，含「executor 無 LLM import」的架構約束測試）、Bash E2E（chaos 場景 CM-01~08、服務修復場景 SR-*）。
- **資料庫遷移**：版本化 migrations（hypertable、retention/compression policy）。
- **文件**：完整的系統說明、部署 runbook、測試報告，以及記錄技術選型過程的 ADR（如 Pydantic schema 驗證、Map-Reduce 異常聚合、runner.v1 取代 command catalog 的安全模型演進）。
- **擴展模式**：新增受支援服務有明確的 8 步驟範本（wrapper → runner → sudoers → Layer 2 分支 → Alloy source → E2E 場景），PostgreSQL/Redis/MySQL 即依此模式從 nginx 範本擴展而來。

---

## 7. 我學到/解決的有趣問題

- **MySQL 修復鏈的邊界案例**：config discovery 必須區分 client config 與 server config；`data dir not found` 這類 runtime 錯誤應歸類為 config repair 而非 restart；快照只能在健康 baseline 上刷新——這些都是 live demo 中暴露、再回頭修正設計的真實案例。
- **Loki 游標推進策略**：高流量窗口下，游標必須推進到「本批最新 log timestamp」而非輪詢結束時間，否則會跳過後段錯誤日誌（Redis 壞設定場景實際踩到）。
- **安全邊界的演進**：從靜態 command catalog 白名單，演進為 argv runner + context-aware hook + 完整稽核的 `runner.v1` 模型——理解到「白名單」不等於「安全」，context 才是長期可擴展的安全判斷依據。

---

## 8. 相關連結

- 原始碼：[github.com/bs10081/AADS](https://github.com/bs10081/AADS)
- 系統詳細說明：[AADS-System-Overview.md](AADS-System-Overview.md)
- 部署指南：[../DEPLOYMENT.md](../DEPLOYMENT.md)
- 測試文件：[AADS-Testing.md](AADS-Testing.md)
