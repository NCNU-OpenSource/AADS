# AADS 系統說明文件

**AI Auto Debug System** — 自動偵測、診斷、修復伺服器服務故障的系統

**文件版本**: 1.0 ｜ **更新日期**: 2026-06-02 ｜ **對應分支**: `codex/ubuntu-agent-layer0-4`

---

## 1. 系統定位

AADS 是一套「**觀測 → 診斷 → 修復**」的自動化閉環系統。它持續收集目標主機的系統日誌，用 LLM 進行根因分析並產生修復計畫，經過人工審批閘道後，由確定性執行器在目標機器上執行修復動作。

核心設計原則是 **「寧可停下，不可亂動」**：每一個邊界情況（無快照、過期授權、並行衝突、上游故障）都傾向安全地阻擋，而不是在不確定狀態下執行修改。

### 設計目標

| 目標 | 實現方式 |
|------|---------|
| 安全 | 窄 catalog API、root-owned sudo wrapper、exact-command sudoers、無任意 shell |
| 可控 | 人工審批閘道、30 分鐘授權窗口、idempotency key |
| 可回復 | 每個修復前先建立 known-good 快照，失敗自動 rollback |
| 可恢復 | 執行器狀態存 DB，crash 後從 DB 而非記憶體恢復 |
| 防注入 | LLM 只能用 catalog 內的 command_id，affected_service 來自觀測標籤 |

---

## 2. 架構：Three-Agent + Gate

系統從原本的 Layer 0–4 命名演進為 **Three-Agent + Gate** 模型，但程式碼目錄仍沿用 layer 命名。

```
┌─────────────┐   logs   ┌──────────────┐  anomalies  ┌───────────────┐
│ On-Device   │─────────▶│  Layer 1     │────────────▶│  System Agent │
│ Agent       │  (Alloy) │  Filter      │             │  (Layer 2)    │
│ (pi-agent)  │          │  異常過濾     │             │  LLM 根因分析  │
└─────────────┘          └──────────────┘             └───────┬───────┘
      ▲                                                        │ FixingPlan v2
      │ catalog API                                            ▼
      │ (probes/actions)                              ┌─────────────────┐
      │                                               │  Gate           │
      │                                               │  (dashboard)    │
      │                                               │  人工審批閘道    │
      │                                               └────────┬────────┘
      │                                                        │ approved + execute
      │                                               ┌────────▼────────┐
      └───────────────────────────────────────────── │ Knowledge Agent │
            per-step HTTP 執行修復                      │  (Layer 4)      │
                                                       │  確定性執行器    │
                                                       └─────────────────┘
```

### 2.0 完整元件對照表（每個元件屬於哪一層、在做什麼）

> 程式碼目錄沿用 Layer 0–4 命名；下表把**每個目錄**與**每個執行中的 container**對應到所屬層級。
> 執行位置：**C** = controller VM（Docker container）、**T** = target VM（systemd 服務）。

| 層 | 元件 / Container | 目錄 | 位置 | 角色 |
|----|-----------------|------|------|------|
| **L0 收集** | Alloy | （image）+ `layer0-collector/alloy/` | C+T | 日誌轉發代理：tail 各服務 log → 推送 Loki |
| **L0 收集** | Loki | （image）+ `layer0-collector/loki/` | C | 日誌聚合儲存（tenant `raw`） |
| **L0 收集** | `log-archiver` | `layer0-storage/sync/log_archiver.py` | C | 從 Loki 拉 raw log → 持久化到 TimescaleDB |
| **L0 收集** | auditd / logrotate 設定 | `layer0-collector/{auditd,logrotate}/` | T | 目標主機的稽核與輪替設定 |
| **L1 過濾** | `layer1-filter` | `layer1-filter/src/` | C | 輪詢 Loki → pattern/LogBERT 過濾 → 寫 `anomaly_logs` |
| **L1 過濾** | `ingester` | `layer1-filter/ingester/` | C | Fan-out Sink A：接收 Alloy HTTP POST → 寫 `anomaly_logs` |
| **L2 診斷** | `layer2-analyzer`（System Agent） | `layer2-analyzer/src/` | C | 消費異常 → LLM 根因分析 → 產生 FixingPlan v2 |
| **L2 診斷** | `litellm` | （image）+ `litellm/` | C | LLM 閘道/代理（System Agent 透過它呼叫模型） |
| **L3 建議/通知** | （已併入 L2）SuggestionGenerator + NotificationHub | `layer2-analyzer/src/{suggestion_generator,notification_hub}.py` | C | 產生修復建議、發通知；由 L2 的 `_run_layer3()` 呼叫 |
| **L3（舊版獨立）** | 原 layer3-remediation | `layer3-remediation/` | — | **已被 L2 取代**，未啟動為服務（保留參考） |
| **Gate 閘道** | `dashboard`（Gate Console） | `dashboard/` | C | 人工審批：approve / reject / execute |
| **L4 執行** | `layer4-executor`（Knowledge Agent） | `layer4-executor/src/executor.py` | C | 確定性順序執行器，per-step 呼叫 On-Device Agent |
| **目標代理** | On-Device Agent | `pi-agent/` | T | catalog API + sudo wrapper，實際在目標機執行 probe/action |
| **資料層** | `timescaledb` | （image） | C | 12 張表的狀態與稽核儲存 |
| **觀測支援** | Prometheus / cadvisor / dcgm-exporter / Grafana | （images） | C | 指標收集與視覺化（供 L2 metrics 關聯用） |

> **三個容易混淆的點**：
> 1. `layer0-collector/` 是**設定目錄**（Alloy/Loki/auditd/logrotate 的 config），collector 本身是 Alloy/Loki image，不是自建服務。
> 2. **Layer 3 已併入 Layer 2**：`layer2-analyzer` 在診斷後會跑 `_run_layer3()` 產生建議與通知。獨立的 `layer3-remediation/` 是舊版，未啟動。
> 3. `ingester` 與 `layer1-filter` 都 build 自 `layer1-filter/` 但**角色不同**：`ingester` 是被動接收 Alloy POST 的 sink，`layer1-filter` 是主動輪詢 Loki 的過濾器。

---

### 2.1 On-Device Agent （`pi-agent/` ｜ 目標代理 ｜ 跑在 target VM）

跑在**目標主機**上的無狀態代理（FastAPI），暴露一個**窄 catalog API**。它從不接受任意 shell；所有特權操作都透過 root-owned 的 sudo wrapper 腳本完成。

**API 端點**：
- `GET /health` — 健康檢查（wrapper/sudoers/snapshot 缺一即 fail-fast）
- `GET /v1/node/facts` — 回傳此節點支援的所有 catalog 指令
- `POST /v1/probes/run` — 執行唯讀探測（probe scope）
- `POST /v1/actions/run` — 執行修復動作（action scope）
- `POST /v1/agent-tasks/run` — 保留給未來的 target-side RCA（v1 未啟用）

**安全邊界（三層）**：
1. **Catalog 白名單**：只有 catalog 內的 `command_id` 可被執行，schema/scope 不符即 400/403
2. **arg_allowlist**：參數層級管制（如 docker container 名、journal unit 名、http_check URL prefix）
3. **sudo wrapper + sudoers**：實際特權操作由 root-owned 腳本完成，sudoers 用 exact-command NOPASSWD 授權

**Catalog（32 個指令，跨 5 個服務）**：

| 服務 | Probe（唯讀） | Action（修復） |
|------|--------------|---------------|
| nginx | status, config_test, http_check | start, reload, ensure_known_good_snapshot, restore_known_good_config |
| postgresql | status, connection_test, config_test | restart, reload, ensure_config_snapshot, restore_known_good_config |
| redis | status, ping, config_test | restart, reload, ensure_config_snapshot, restore_known_good_config |
| docker | container_status | container_restart, container_start |
| mysql | status, connection_test, config_test | restart, reload, ensure_config_snapshot, restore_known_good_config |
| system | journal_tail | — |

### 2.2 Layer 0：資料收集（`layer0-collector/`、`layer0-storage/` ｜ 跑在 C+T）

把目標主機的日誌可靠地送進可查詢的儲存。**這層沒有自建程式（除 log-archiver 外），主要是現成工具 + 設定。**

| 元件 | 檔案/位置 | 做什麼 |
|------|----------|--------|
| **Alloy**（agent） | `layer0-collector/alloy/config.alloy`（target 上 `/etc/alloy/config.alloy`） | tail 各服務 log（nginx/pg/redis/mysql/syslog）→ HTTP push 到 controller 的 Loki。每條帶 `node_id`/`service`/`job` 標籤 |
| **Loki** | `layer0-collector/loki/loki-config.yaml` | 日誌聚合儲存，tenant `raw`；L1 從這裡查詢 |
| **log-archiver** | `layer0-storage/sync/log_archiver.py` | 獨立 container：從 Loki 拉 raw log → 寫入 TimescaleDB `raw_logs`（長期保存） |
| **auditd / logrotate** | `layer0-collector/{auditd,logrotate}/` | target 主機的稽核規則與日誌輪替設定 |

> Alloy 的 log source 清單決定了「哪些服務的日誌看得到」。擴展新服務必須在此加一個 `loki.source.file`。

### 2.3 Layer 1：異常過濾（`layer1-filter/` ｜ container `layer1-filter` + `ingester` ｜ 跑在 C）

把海量日誌過濾成少量**異常**寫進 `anomaly_logs`。同一個目錄 build 出兩個角色不同的服務：

| Container | 入口 | 角色 |
|-----------|------|------|
| **layer1-filter** | `layer1-filter/src/main.py` → `src/pipeline.py` + `filters/` | **主動**輪詢 Loki → pattern filter（可選 LogBERT）→ 寫 `anomaly_logs` |
| **ingester** | `layer1-filter/ingester/main.py` | **被動** Fan-out Sink A：接收 Alloy 直送的 HTTP POST → 寫 `anomaly_logs` |

- 查詢由 `LAYER1_LOKI_QUERY` 控制。**已從 `{source="target-nginx"}` 改為 `{node_id="..."}`** 以涵蓋節點上所有服務。
- 上游 Loki 不可用時靜默降級，不產生誤報（見 Chaos CM-03）。

### 2.4 Layer 2：System Agent（`layer2-analyzer/` ｜ container `layer2-analyzer` + `litellm` ｜ 跑在 C）

LLM **只用於**根因分析（RCA）+ 產生 FixingPlan v2 + self-check。這是整個系統最複雜的元件，子模組如下：

| 子模組 | 檔案 | 職責 |
|--------|------|------|
| 異常消費 | `anomaly_consumer.py` | 每 30s 輪詢 `anomaly_logs`，取出未處理的異常 |
| 聚合 | `aggregator/map_reduce.py`、`root_cause_analyzer/anomaly_aggregator.py` | 把零散異常聚成 cluster（Map-Reduce 摘要） |
| LLM 推理 | `root_cause_analyzer/llm_reasoner.py`、`agent/graph.py`、`agent/tools.py` | LangGraph agent：用 query_loki/prometheus 等工具產生 `ClaudeStylePlan`；system prompt 含各服務故障→動作對應 |
| LLM 客戶端 | `llm/{openai_compatible,ollama,base}.py` | 抽象 LLM 介面，透過 `litellm` 閘道呼叫模型 |
| 指標關聯 | `root_cause_analyzer/metrics_correlator.py` | 從 Prometheus 拉 CPU/記憶體/GPU 佐證 |
| 原始日誌 | `raw_log_fetcher.py` | 取回 cluster 對應的原始 log 供 LLM 參考 |
| 計畫組裝 | `main.py`（orchestrator） | `_ensure_lab_node_agent_steps` / `_to_fixing_plan` 等（見下方流程） |
| Schema | `schemas/action_plan.py` | Pydantic：`ClaudeStylePlan`（展示用）、`FixingPlan` v2（執行用）、`RootCauseReport` |
| **L3 子階段** | `suggestion_generator.py`、`notification_hub.py` | 診斷後 `_run_layer3()` 產生建議 + 發通知（原 Layer 3，已併入此處） |

**計畫組裝流程（`main.py`）**：
1. 從 `anomaly_logs` 取出異常 cluster
2. LLM Agent 分析產生 `ClaudeStylePlan`（展示用 TODO List）
3. `_ensure_lab_node_agent_steps()`：**確定性後處理**，依異常訊息/服務標籤偵測服務（nginx/pg/redis/mysql），注入對應 catalog 步驟
   - 服務停止 → `<svc>.restart` / `nginx.start`
   - **特定** config error 訊號（invalid line、syntax error、can't open config…）→ `<svc>.restore_known_good_config`
4. `_to_fixing_plan()`：轉成 schema 2.0 可執行 FixingPlan
   - `pre_execution_snapshot`：**service-aware**，`_snapshot_command_for()` 依服務選 `<svc>.ensure_config_snapshot`
   - `final_verification`：**service-aware**，`_final_verification_for()` 依服務選 `<svc>.connection_test` / `redis.ping` / `nginx.http_check`
5. Pydantic 驗證後寫入 `diagnosis_reports`

**防注入**：`affected_service` 只能來自觀測標籤；`command_id` 綁定 catalog，LLM 無法發明指令；日誌一律視為資料。

**重要**：若產生不出可執行步驟，會 fallback 成 schema 1.0（`*_fallback` diagnosis_id），Gate 會拒絕審批 → 代表 L2 某處出錯（過去遇過：空 `affected_service`/`root_cause` 觸發 Pydantic 例外）。

### 2.5 Gate：審批閘道（`dashboard/` ｜ container `dashboard` ｜ 跑在 C）

人工審批閘道（Flask + Direction-B 兩欄式 Console UI）。

- **Admin-key 認證**（`X-Admin-API-Key`）
- **approve / reject / execute** 三個動作
- **30 分鐘授權窗口**：approve 後逾時 execute 會被 403 拒絕（見 Chaos CM-05）
- **Idempotency 矩陣**：同 key 重送回 200/400/409
- **Schema 2.0 guard**：approve 與 execute 都檢查 plan 必須是 schema 2.0 且有可執行步驟，否則拒絕

### 2.6 Layer 4：Knowledge Agent / Executor（`layer4-executor/` ｜ container `layer4-executor` ｜ 跑在 C）

確定性順序執行器（`src/executor.py`），**無任何 LLM import**（單元測試保證）。透過 HTTP 呼叫 target 上的 On-Device Agent 來實際執行。

**執行流程**：
1. 取得 node lock（TTL = max(timeout×2, 120s)，finally 釋放，啟動時 sweep 過期 lock）
2. `pre_execution_snapshot`：先建立 known-good 快照；無快照且 `on_failure=block` → 阻擋
3. 依序執行每個 step 的 catalog 指令（per-step HTTP 呼叫 On-Device Agent）
4. 每步有 verification；失敗依 `on_failure` 決定 rollback 或 abort
5. `final_verification` 確認整體修復成功
6. 終態寫入 `plan_executions.status`

**終態（terminal states）**：
`kb_skipped`（成功但不匯入知識）、`kb_imported`、`final_verified`、`blocked`（被安全規則擋下）、`failed_retryable`（暫時性失敗，可重試）、`execution_failed`、`execution_failed_unknown_state`

### 2.7 資料層：TimescaleDB（container `timescaledb` ｜ 跑在 C ｜ 12 張表）

跨所有層的狀態與稽核儲存。各表大致歸屬：

| 層 | 表 |
|----|-----|
| L0 | `raw_logs` |
| L1 | `anomaly_logs` |
| L2 | `diagnosis_reports`、`knowledge_cases` |
| Gate | `plan_approvals`、`idempotency_records` |
| L4 | `plan_executions`、`execution_steps`、`node_locks` |
| 跨層 | `agent_nodes`（節點/catalog 註冊）、`agent_tasks`、`audit_events`（全程稽核） |

---

## 3. 修復的四層架構模式

每個受支援的服務都遵循同一套模式（這是擴展新服務的範本）：

```
Layer 1 (偵測)      →  Layer 2 (診斷+計畫)      →  pi-agent catalog (執行)   →  E2E 測試 (驗證)
Alloy 收集日誌        FixingPlan v2 步驟          sudo wrapper 在目標執行      SR-* 場景
LOKI_QUERY 過濾       service-aware 後處理         arg_allowlist + sudoers
```

**擴展新服務的清單**（以 PostgreSQL 為例）：
1. 5 個 wrapper：`restart` / `reload` / `config-test` / `ensure-config-snapshot` / `restore-config`
2. `pi-agent/src/main.py`：catalog 指令 + probe handler + WRAPPERS + health 檢查
3. sudoers 加入 exact-command 授權
4. systemd unit 的 `ReadWritePaths` 加入該服務的 config 與 log 路徑
5. Layer 2 `_ensure_lab_node_agent_steps` / `_final_verification_for` / `_snapshot_command_for` / `_expected_outcome_for` / `_verification_for` 加入該服務分支
6. Layer 2 system prompt 加入該服務的故障→動作對應
7. Alloy config 加入該服務的 log source
8. SR-* E2E 測試場景

---

## 4. 安全模型重點

| 機制 | 防範的風險 |
|------|-----------|
| 窄 catalog + 無任意 shell | LLM 或攻擊者注入任意指令 |
| root-owned wrapper + exact sudoers | 提權、wrapper 被竄改 |
| arg_allowlist（如 docker 容器白名單） | 對非授權目標執行動作 |
| pre-execution snapshot 強制 | 在沒有回滾基準的狀態下亂動（CM-07 / SR-PG-03） |
| 30 分鐘授權窗口 | 過期授權被利用（CM-05） |
| node lock | 並行修復互相干擾（CM-06） |
| DB-driven 恢復 | executor crash 後重複執行（CM-02） |
| 上游故障靜默降級 | 資料來源斷裂時誤判/誤修（CM-03 / CM-04） |
| 失敗釋放 lock + failed_retryable | network/agent 故障造成 lock 洩漏（CM-01 / CM-08） |

---

## 5. 實驗環境（Multipass Lab）

- **aads-controller**（192.168.252.2）：跑所有 Docker 服務（Layer 1/2/4、Gate、Loki、Grafana、Prometheus、TimescaleDB、LiteLLM 等）
- **aads-target**（192.168.252.3）：Ubuntu 24.04，被監控的目標主機，跑 On-Device Agent + 業務服務（nginx、postgresql、redis、mysql）+ Alloy

**啟動/管理腳本**（`scripts/lab/`）：`up.sh`、`down.sh`、`sync-controller.sh`、`install-target-alloy.sh`、`check-prereqs.sh`

⚠️ **部署注意**：layer2-analyzer 程式碼是 baked 進 Docker image，改動後需 `docker compose build`（force-recreate 不夠）。pi-agent wrapper 需 transfer 到 target 的 `/usr/local/sbin/`。詳見 [service-coverage-handoff.md](service-coverage-handoff.md) §4。

---

## 6. 相關文件

- **測試說明**：[AADS-Testing.md](AADS-Testing.md) — Chaos E2E 與 Service-Coverage E2E 完整場景
- **Codex 交接**：[service-coverage-handoff.md](service-coverage-handoff.md) — 服務擴展現況與待辦
