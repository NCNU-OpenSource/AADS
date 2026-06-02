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

### 2.1 On-Device Agent （`pi-agent/`）

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

### 2.2 Layer 1 Filter （`layer1-filter/`）

從 Loki 拉取日誌，用 pattern filter（可選 LogBERT）過濾出**異常**，寫入 `anomaly_logs`。

- 查詢由 `LAYER1_LOKI_QUERY` 控制。**已從 `{source="target-nginx"}` 改為 `{node_id="..."}`** 以涵蓋節點上所有服務（PostgreSQL/Redis/MySQL）。
- 上游 Loki 不可用時靜默降級，不產生誤報（見 Chaos CM-03）。

### 2.3 System Agent （`layer2-analyzer/`）

LLM **只用於**根因分析（RCA）+ 產生 FixingPlan v2 + self-check。

**處理流程**：
1. 從 `anomaly_logs` 取出異常 cluster
2. LLM Agent 分析（query_loki / query_prometheus / 診斷指令）產生 `ClaudeStylePlan`
3. `_ensure_lab_node_agent_steps()`：**確定性後處理**，依據異常訊息/服務標籤偵測服務（nginx/postgresql/redis/mysql），注入對應的 catalog 修復步驟
   - 服務停止 → `<svc>.restart` / `nginx.start`
   - **特定** config error 訊號（invalid line、syntax error、can't open config 等）→ `<svc>.restore_known_good_config`
4. `_to_fixing_plan()`：轉成 schema 2.0 可執行 FixingPlan
   - `pre_execution_snapshot`：**service-aware**，依服務選 `<svc>.ensure_config_snapshot`
   - `final_verification`：**service-aware**，依服務選 `<svc>.connection_test` / `redis.ping` / `nginx.http_check`
5. Pydantic 驗證後寫入 `diagnosis_reports`

**防注入**：
- `affected_service` 只能來自觀測到的 container/service 標籤
- `command_id` 綁定 catalog，LLM 無法發明指令
- 日誌內容一律視為資料，不視為指令

**重要**：若產生不出可執行步驟，會 fallback 成 schema 1.0（`*_fallback` diagnosis_id），Gate 會拒絕審批。這代表 Layer 2 在某處出錯（過去遇過的：空 `affected_service`/`root_cause` 觸發 Pydantic 例外）。

### 2.4 Gate （`dashboard/`）

人工審批閘道（Flask + Direction-B 兩欄式 Console UI）。

- **Admin-key 認證**（`X-Admin-API-Key`）
- **approve / reject / execute** 三個動作
- **30 分鐘授權窗口**：approve 後逾時 execute 會被 403 拒絕（見 Chaos CM-05）
- **Idempotency 矩陣**：同 key 重送回 200/400/409
- **Schema 2.0 guard**：approve 與 execute 都檢查 plan 必須是 schema 2.0 且有可執行步驟，否則拒絕

### 2.5 Knowledge Agent / Executor （`layer4-executor/`）

確定性順序執行器，**無任何 LLM import**（單元測試保證）。

**執行流程**：
1. 取得 node lock（TTL = max(timeout×2, 120s)，finally 釋放，啟動時 sweep 過期 lock）
2. `pre_execution_snapshot`：先建立 known-good 快照；無快照且 `on_failure=block` → 阻擋
3. 依序執行每個 step 的 catalog 指令（per-step HTTP 呼叫 On-Device Agent）
4. 每步有 verification；失敗依 `on_failure` 決定 rollback 或 abort
5. `final_verification` 確認整體修復成功
6. 終態寫入 `plan_executions.status`

**終態（terminal states）**：
`kb_skipped`（成功但不匯入知識）、`kb_imported`、`final_verified`、`blocked`（被安全規則擋下）、`failed_retryable`（暫時性失敗，可重試）、`execution_failed`、`execution_failed_unknown_state`

### 2.6 資料庫（TimescaleDB，12 張表）

`raw_logs`, `anomaly_logs`, `diagnosis_reports`, `agent_nodes`, `plan_approvals`, `plan_executions`, `idempotency_records`, `agent_tasks`, `execution_steps`, `node_locks`, `audit_events`, `knowledge_cases`

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
