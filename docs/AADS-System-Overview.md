# AADS 系統說明文件

**AI Auto Debug System** — 自動偵測、診斷、修復伺服器服務故障的系統

**文件版本**: 1.2 ｜ **更新日期**: 2026-06-17 ｜ **對應分支**: `feature/execution-hardening`

---

## 0. Demo Lab 快速入口

目前 demo 環境的主要入口如下。Gate Console 是人工審批與展示修復流程的主畫面；其他服務主要用於除錯、觀測與查證。

> 下表位址以 `${AADS_CONTROLLER_IP}` / `${AADS_TARGET_IP}` 佔位，實際 IP 以 `.env.lab` 與當前 lab 拓撲為準（不要把真實 IP 寫進文件或 commit）。

| 用途 | 位址 | 備註 |
|------|------|------|
| **AADS Gate Console / Dashboard** | `http://${AADS_CONTROLLER_IP}:5000/` | demo 時主要開這個；Approve 與 Execute 需輸入 Admin Key |
| Grafana | `http://${AADS_CONTROLLER_IP}:3000/` | 指標與 Loki/Grafana dashboard；compose 設定允許 anonymous viewer |
| Prometheus | `http://${AADS_CONTROLLER_IP}:9090/` | metrics 查詢與健康檢查 |
| Loki API | `http://${AADS_CONTROLLER_IP}:3100/` | Layer 1 查詢 raw log 的來源 |
| Layer 2 Analyzer API | `http://${AADS_CONTROLLER_IP}:8080/` | System Agent webhook/API |
| cAdvisor | `http://${AADS_CONTROLLER_IP}:8081/` | controller container resource metrics（容器內 8080） |
| Target On-Device Agent | `http://${AADS_TARGET_IP}:8090/` | 需 Bearer token；`/health` 未帶 token 會回 401，屬正常保護 |

目前 lab 拓撲：

| 角色 | 位址 | 說明 |
|------|------|------|
| Controller | `${AADS_CONTROLLER_IP}` | 跑 Docker Compose stack、Gate、Layer 1/2/4、Loki、Prometheus、Grafana、TimescaleDB、LiteLLM |
| Target | `${AADS_TARGET_IP}` | 被監控/被修復的節點，跑 nginx、PostgreSQL、Redis、MySQL/MariaDB、Alloy、`aads-agent` |
| Target node id | `${AADS_TARGET_NODE_ID}` | Layer 1 `LAYER1_LOKI_QUERY` 與 Gate plan 會用它定位目標節點 |

> **Secret 原則**：Dashboard 的 Admin Key 與 pi-agent Bearer token 只放在本機 `.aads-lab-admin-key` / `.aads-lab-token` 或 controller `.env.lab`，不要寫進文件或 commit。

## 1. 系統定位

AADS 是一套「**觀測 → 診斷 → 修復**」的自動化閉環系統。它持續收集目標主機的系統日誌，用 LLM 進行根因分析並產生修復計畫，經過人工審批閘道後，由確定性執行器在目標機器上執行修復動作。

核心設計原則是 **「寧可停下，不可亂動」**：每一個邊界情況（無快照、過期授權、並行衝突、上游故障）都傾向安全地阻擋，而不是在不確定狀態下執行修改。

### 設計目標

| 目標 | 實現方式 |
|------|---------|
| 安全 | argv runner + hook/audit、單一 root runner、無任意 shell（shell=False） |
| 可控 | 人工審批閘道、30 分鐘授權窗口、idempotency key |
| 可回復 | 每個修復前先建立 known-good 快照，失敗自動 rollback |
| 可恢復 | 執行器狀態存 DB，crash 後從 DB 而非記憶體恢復 |
| 防注入 | LLM 產出 (service, operation)→runner spec，affected_service 來自觀測標籤；ExecutionProfile/PolicyCard fail-closed 強制 + `log_guard` taint→強制人工審查 |

---

### 1.1 完整技術棧與框架

這份 repo 不是單一 Web app，而是一個由 controller stack、target agent、觀測資料管線、LLM 診斷器與確定性修復器組成的 lab 系統。下表列出目前實際使用的框架、套件與責任邊界。

| 區塊 | 主要框架 / 工具 | 在系統中的角色 |
|------|----------------|----------------|
| Runtime / 部署 | Docker Compose、Docker Engine、Linux systemd、Bash、SSH、sudoers、Multipass/Ubuntu lab、OrbStack（macOS local debug） | controller 服務以 Compose 管理；target 代理與業務服務用 systemd；部署與 demo 用 Bash/SSH 腳本 |
| Gate Console / Dashboard | Flask 3.0、asyncpg 0.29、React 18 UMD、ReactDOM、Babel Standalone、vanilla CSS/JS、localStorage、Fetch API | Flask 提供 API 與 `index.html`；React/Babel 直接由瀏覽器載入，無 Node build step；UI 管 approve / reject / execute / trace |
| On-Device Agent | FastAPI 0.109、Uvicorn 0.27、Pydantic 2.12+、Python `subprocess.run(shell=False)`、systemd、root runner wrapper、service wrapper scripts | target 端 `/health`、`/v1/node/facts`、`/v1/commands/run`；所有修復動作以 argv runner spec 執行 |
| Layer 0 日誌與長期儲存 | Grafana Alloy、Grafana Loki 3.6、TimescaleDB/PostgreSQL 16、Python log-archiver、aiohttp、asyncpg | Alloy tail target log；Loki 作短期查詢；log-archiver 將 raw logs 落到 TimescaleDB `raw_logs` |
| Layer 1 異常過濾 | Python、asyncpg、aiohttp、requests、Drain3、PyTorch 2.1、Transformers 4.35、NumPy、PyYAML、FastAPI/Uvicorn（ingester） | `layer1-filter` 主動輪詢 Loki；`ingester` 被動接收 fan-out；pattern filter 與可選 LogBERT 寫入 `anomaly_logs` |
| Layer 2 System Agent | FastAPI、Uvicorn、asyncpg、aiohttp、Pydantic 2.7、OpenAI Python SDK、LangChain、LangGraph、langchain-openai、LiteLLM、可選 Ollama | 消費 anomaly、聚合 cluster、用 LLM 做 RCA、產生展示用 `ClaudeStylePlan` 與可執行 `FixingPlan 3.1`（含確定性產生的 `execution_profile`） |
| Layer 3 建議/通知 | Python suggestion generator、notification hub、Slack/Webhook | 舊 Layer 3 已併入 Layer 2 `_run_layer3()`；負責修復建議與通知，不再獨立啟動 |
| Layer 4 Knowledge Agent / Executor | Python、asyncpg、aiohttp、Pydantic、DB-driven state machine | 無 LLM import；從 DB 取 queued execution，拿 node lock，逐步呼叫 target `/v1/commands/run` |
| Observability | Prometheus、Grafana、cAdvisor、DCGM Exporter（GPU profile）、Grafana Alloy debug UI | 提供 metrics、container resource、GPU metrics 與 dashboard 視覺化 |
| LLM Gateway | LiteLLM、OpenAI-compatible upstream API、可選 Ollama | 統一模型 API；Layer 2 只打 `http://litellm:4000/v1` |
| Database / Schema | TimescaleDB hypertable、PostgreSQL JSONB、migrations `000`-`005` | 13 張核心表：logs、anomalies、diagnosis、approvals、executions、locks、audit、agent registry、knowledge cases，外加 `execution_escalations`（drift/暫停佇列，migration 005）；005 同時為 `plan_approvals` 加上 `plan_sha256` 欄位 |
| 測試 / Demo | pytest、pytest-asyncio、curl、psql、Bash E2E scripts、macOS `gtimeout` fallback | 單元測試覆蓋 runner/executor/L2；`chaos-e2e.sh` 與 `e2e-service-repair.sh` 用於自動驗證；`demo-nginx-manual-gate.sh` 用於手動 Dashboard demo |

**Dashboard 前端特性**：
- `dashboard/templates/index.html` 直接載入 React 18、ReactDOM、Babel Standalone 與 `dashboard/static/app/*.jsx`。
- `dashboard/app.py` 同時扮演 Web server、Gate API、Agent registry API、execution trace API。
- UI 的 Admin Key 存在 browser `localStorage`；server 只用 `X-Admin-API-Key` 比對，不回傳 secret，只回 length/fingerprint 診斷。

**後端服務通訊**：
- controller 內部 container 走 Compose network `observability`。
- target 的 `aads-agent` 由 controller 經 HTTP + Bearer token 呼叫。
- Gate approve/execute 寫 DB；`layer4-executor` 輪詢 DB，真正執行修復。

### 1.2 操作手冊：一次完整修復如何發生

1. Target 上的 Alloy tail nginx/PostgreSQL/Redis/MySQL/syslog，將 log 推到 controller Loki。
2. `layer1-filter` 用 `LAYER1_LOKI_QUERY={node_id="..."}` 輪詢 Loki，把異常寫入 TimescaleDB `anomaly_logs`。
3. `layer2-analyzer` 每 30 秒消費 anomaly，聚合成 cluster，呼叫 LiteLLM/LLM 做 RCA。
4. Layer 2 先產生展示用 `ClaudeStylePlan`，再用 deterministic post-process 轉為 `FixingPlan 3.1`（由 `runner_catalog.execution_profile_for` 確定性產生 `execution_profile` 權限清單）。
5. `diagnosis_reports` 出現 `pending_approval`，Dashboard Queue 會顯示一筆可審批 plan。
6. Operator 在 Dashboard 輸入 Admin Key，按 **Approve**，Gate 寫入 `plan_approvals`（含 `plan_sha256` 綁定當下 plan 內容）與 audit event。
7. Operator 再按 **Execute**，前端送 `Idempotency-Key`；Gate 建立 `plan_executions(status=queued)`。
8. `layer4-executor` 取得 queued execution，先拿 `node_locks`，執行 pre-execution snapshot。
9. Executor 按順序呼叫 target `/v1/commands/run`，target 以 argv runner + hook/audit + sudo root runner 實作修復。
10. 每一步與 final verification 都寫入 `execution_steps`、`plan_executions.result`、`audit_events`。
11. 成功時 lab 常見終態為 `kb_skipped`（因 `ENABLE_KNOWLEDGE_BASE=false`）；若啟用知識庫則可進入 `kb_imported`。

### 1.3 重要設定檔與部署腳本

| 類型 | 位置 | 用途 |
|------|------|------|
| Controller compose | `docker-compose.yaml` | 宣告 Loki、Prometheus、Grafana、TimescaleDB、Layer 1/2/4、Dashboard、LiteLLM |
| Controller env | `.env.lab`（不 commit secret） | controller demo 實際使用的 IP、token、admin key、LLM upstream、Gate policy |
| Target env | `/etc/aads-agent/agent.env` | target 的 agent token、node id path、environment、root runner path |
| Alloy config | `layer0-collector/alloy/config.alloy` 與 target `/etc/alloy/config.alloy` | 決定哪些 service log 會進 Loki |
| DB migrations | `layer0-storage/timescaledb/migrations/` | 建表、hypertable、retention/compression、runner capabilities migration |
| Controller deploy | `scripts/lab/deploy-controller.sh` | rebuild/restart controller-side Docker service，預設 `layer2-analyzer` |
| Target deploy | `scripts/lab/deploy-target.sh` | 安裝 `pi-agent/`、systemd unit、root runner、service wrappers |
| Full lab bootstrap | `scripts/lab/up.sh`、`scripts/lab/down.sh` | 建立/停止 lab VM 與 controller stack |
| Demo/E2E | `scripts/lab/chaos-e2e.sh`、`scripts/lab/e2e-service-repair.sh`、`scripts/lab/demo-nginx-manual-gate.sh` | 前兩者是自動驗證；最後一支是保留人工 Approve/Execute 的 Nginx Dashboard demo |

---

## 2. 架構：Three-Agent + Gate

系統從原本的 Layer 0–4 命名演進為 **Three-Agent + Gate** 模型，但程式碼目錄仍沿用 layer 命名。

```
┌─────────────┐   logs   ┌──────────────┐  anomalies  ┌───────────────┐
│ On-Device   │─────────▶│  Layer 1     │────────────▶│  System Agent │
│ Agent       │  (Alloy) │  Filter      │             │  (Layer 2)    │
│ (pi-agent)  │          │  異常過濾     │             │  LLM 根因分析  │
└─────────────┘          └──────────────┘             └───────┬───────┘
      ▲                                                        │ FixingPlan 3.1
      │ runner API                                              ▼
      │ (/v1/commands/run)                              ┌─────────────────┐
      │                                               │  Gate           │
      │                                               │  (dashboard)    │
      │                                               │  人工審批閘道    │
      │                                               └────────┬────────┘
      │                                                        │ approved + execute
      │                                               ┌────────▼────────┐
      └───────────────────────────────────────────── │ Knowledge Agent │
            per-step runner 執行修復                      │  (Layer 4)      │
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
| **L2 診斷** | `layer2-analyzer`（System Agent） | `layer2-analyzer/src/` | C | 消費異常 → LLM 根因分析 → 產生 FixingPlan 3.1（含 `execution_profile`） |
| **L2 診斷** | `litellm` | （image）+ `litellm/` | C | LLM 閘道/代理（System Agent 透過它呼叫模型） |
| **L3 建議/通知** | （已併入 L2）SuggestionGenerator + NotificationHub | `layer2-analyzer/src/{suggestion_generator,notification_hub}.py` | C | 產生修復建議、發通知；由 L2 的 `_run_layer3()` 呼叫 |
| **L3（舊版獨立）** | 原 layer3-remediation | `layer3-remediation/` | — | **已被 L2 取代**，未啟動為服務（保留參考） |
| **Gate 閘道** | `dashboard`（Gate Console） | `dashboard/` | C | 人工審批：approve / reject / execute |
| **L4 執行** | `layer4-executor`（Knowledge Agent） | `layer4-executor/src/executor.py` | C | 確定性順序執行器，per-step 呼叫 On-Device Agent |
| **目標代理** | On-Device Agent | `pi-agent/` | T | argv command runner + hook/audit，實際在目標機執行 runner spec |
| **資料層** | `timescaledb` | （image） | C | 13 張表的狀態與稽核儲存（含 `execution_escalations`） |
| **觀測支援** | Prometheus / cadvisor / dcgm-exporter / Grafana | （images） | C | 指標收集與視覺化（供 L2 metrics 關聯用） |

> **三個容易混淆的點**：
> 1. `layer0-collector/` 是**設定目錄**（Alloy/Loki/auditd/logrotate 的 config），collector 本身是 Alloy/Loki image，不是自建服務。
> 2. **Layer 3 已併入 Layer 2**：`layer2-analyzer` 在診斷後會跑 `_run_layer3()` 產生建議與通知。獨立的 `layer3-remediation/` 是舊版，未啟動。
> 3. `ingester` 與 `layer1-filter` 都 build 自 `layer1-filter/` 但**角色不同**：`ingester` 是被動接收 Alloy POST 的 sink，`layer1-filter` 是主動輪詢 Loki 的過濾器。

---

### 2.1 On-Device Agent （`pi-agent/` ｜ 目標代理 ｜ 跑在 target VM）

> **runner.v1**：已用 **argv-first Command Runner + context-aware Hook** 取代舊的
> catalog command whitelist。安全邊界不再是「`command_id` 是否在 catalog」，而是每次
> execution 的完整 **runner spec + context + PolicyCard/hook 決策 + append-only audit**。
> Agent 自報 `runner_capabilities.schema_version="runner.v1"`（`AGENT_VERSION='2.1.0'`）。

跑在**目標主機**上的無狀態代理（FastAPI），暴露一個 **full-power argv command runner**。它從不接受任意 shell（argv 以 `shell=False` 執行）；特權操作（`as_root=true`）透過**單一** root runner wrapper 完成。

**API 端點**：
- `GET /health` — 健康檢查（node-id / root runner / sudoers 缺一即 fail-fast）
- `GET /v1/node/facts` — 回傳 `runner_capabilities`（schema_version / modes / supports_as_root / hook_default）
- `POST /v1/commands/run` — 執行一個 runner spec（取代 `/v1/probes/run` + `/v1/actions/run`）
- `POST /v1/agent-tasks/run` — 保留給未來的 target-side RCA（v1 未啟用）

**安全邊界（runner.v1）**：
1. **PolicyCard + Hook + Audit**：每次 execution 經 `before_run → execute → after_run`；
   `PolicyCard`（`pi-agent/src/safety_cards/policy_card.py`）已上線，依 plan 的 `execution_profile`
   做 **fail-closed** 比對——預設 `AADS_POLICY_MODE=enforce`，profile 不允許的 argv 直接 `deny`；
   `audit` 模式只警告但放行（debug 用）。`AuditHook` 仍負責 append-only 稽核。
2. **side_effect 驅動鎖**：`runner.side_effect=mutate` 才取 node lock / 觸發 snapshot（不靠 `as_root` 推斷）
3. **單一 root runner + sudoers**：特權只走一個 root-owned wrapper，sudoers 只授權它

> **Legacy removal note**：舊的三層邊界是 catalog 白名單 + arg_allowlist + 每個 wrapper 的
> exact-command sudoers。單純 command whitelist 不具 context-aware safety，已移除。OS 層的
> exact-command 防線降級為單一 root runner，改由 app 層的 **PolicyCard + hook/audit** 承接——
> `PolicyCard` 預設 fail-closed (`enforce`)，profile 是 exact-command grant（`allow_extra_args=False`），
> 因此 `systemctl is-active nginx` 不會授權 `systemctl stop nginx`。詳見下方 ADR-005。

**Runner operations（Layer 2 `(service, operation)` → runner spec 對照，見 `layer2-analyzer/src/runner_catalog.py`）**：

> 這些不再是 agent 端的 catalog metadata，而是 Layer 2 把 `(service, operation)` 翻譯成 argv
> runner spec（read/mutate、as_root、timeout）。agent 端只認 argv，不認 operation 名稱。

| 服務 | read（驗證/probe） | mutate（修復，as_root） |
|------|--------------------|--------------------------|
| nginx | status, config_test, http_check | start, reload, restore_config, ensure_snapshot |
| postgresql | status, connection_test | restart, reload, restore_config, ensure_snapshot |
| redis | status, ping | restart, reload, restore_config, ensure_snapshot |
| mysql | status, connection_test | restart, reload, restore_config, ensure_snapshot |

> `config_test` 是 **nginx 專屬** probe（`nginx -t`）。其他服務沒有 `config_test`，
> 改用 `connection_test`（pg/mysql）或 `ping`（redis）做修復後驗證。nginx 共 7 個 op
> （3 probe + 4 mutation），pg/redis/mysql 各 6 個。catalog 中沒有 docker / system op。

驗證採 **argv + 宣告式 extractor**：`is-active`→`{active=true}`、`curl -w %{http_code}`→`{http_code=200}`、
`redis-cli ping`→`{pong=true}`、`pg_isready`/`mysqladmin ping`→`{returncode=0}`/`{alive=true}`。

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
- Loki 查詢使用 forward direction + batch limit；游標必須推進到「本批最新 log timestamp」而不是 poll end，避免高流量窗口跳過後段錯誤 log（Redis bad-config 曾踩到此問題）。
- 上游 Loki 不可用時靜默降級，不產生誤報（見 Chaos CM-03）。
- **生產用 LogBERT 就在 `layer1-filter`**：`src/filters/logbert_filter.py` 與 pattern filter 經 `_run_fusion`（規則 `pattern_or_logbert`）融合後，結果直接寫 TimescaleDB `anomaly_logs`（不是 JSON、也不 push 回 Loki）。layer1 硬寫 `device='cpu'`，torch 缺席時 graceful degrade（只跑 pattern filter）。repo 內另有獨立的 `logbert/` 原型（GPU、JSON 輸出、push Loki）與此不同，勿混用。

### 2.4 Layer 2：System Agent（`layer2-analyzer/` ｜ container `layer2-analyzer` + `litellm` ｜ 跑在 C）

LLM **只用於**根因分析（RCA）+ 產生可審批的 FixingPlan 3.1 + self-check。這是整個系統最複雜的元件，子模組如下：

> Layer 2 只發 **3.1**（`schemas/action_plan.py` 把 `schema_version` 釘成 `Literal["3.1"]`，且 `execution_profile` 為必填——model 會直接拒絕 3.0）。3.0 只在「消費端」被向下相容接受。LLM 透過 `langchain_openai.ChatOpenAI` 連到 `LLM_BASE_URL`（compose 預設 `http://litellm:4000/v1`，即 LiteLLM 代理），用的是 OpenAI SDK client 但 base_url 指向代理，因此「不直接打 provider」仍成立。

| 子模組 | 檔案 | 職責 |
|--------|------|------|
| 異常消費 | `anomaly_consumer.py` | 每 30s 輪詢 `anomaly_logs`，取出未處理的異常 |
| 聚合 | `aggregator/map_reduce.py`、`root_cause_analyzer/anomaly_aggregator.py` | 把零散異常聚成 cluster（Map-Reduce 摘要） |
| LLM 推理 | `root_cause_analyzer/llm_reasoner.py`、`agent/graph.py`、`agent/tools.py` | LangGraph agent：用 query_loki/prometheus 等工具產生 `ClaudeStylePlan`；system prompt 含各服務故障→動作對應 |
| LLM 客戶端 | `llm/{openai_compatible,ollama,base}.py` | 抽象 LLM 介面，透過 `litellm` 閘道呼叫模型 |
| 指標關聯 | `root_cause_analyzer/metrics_correlator.py` | 從 Prometheus 拉 CPU/記憶體/GPU 佐證 |
| 原始日誌 | `raw_log_fetcher.py` | 取回 cluster 對應的原始 log 供 LLM 參考 |
| 計畫組裝 | `main.py`（orchestrator） | `_ensure_lab_node_agent_steps` / `_to_fixing_plan` 等（見下方流程） |
| Schema | `schemas/action_plan.py` | Pydantic：`ClaudeStylePlan`（展示用）、`FixingPlan` 3.1（執行用，runner-based，含 `execution_profile`）、`ExecutionProfile`、`RootCauseReport`；內含一份 `profile_allows()`（與 pi-agent 同名函式 verdict 對齊，見 `tests/test_parity.py`） |
| ExecutionProfile 產生器 | `runner_catalog.py::execution_profile_for` | 從 catalog 自己發出的 runner spec + 靜態路徑表，**確定性**產生權限 manifest，LLM 不參與 |
| 日誌注入防護 | `log_guard.py` | 確定性 regex scanner + `fence()` 資料圍欄 + contextvars taint registry（ADR-007） |
| **L3 子階段** | `suggestion_generator.py`、`notification_hub.py` | 診斷後 `_run_layer3()` 產生建議 + 發通知（原 Layer 3，已併入此處） |

**計畫組裝流程（`main.py`）**：
1. 從 `anomaly_logs` 取出異常 cluster
2. LLM Agent 分析產生 `ClaudeStylePlan`（展示用 TODO List）
3. `_ensure_lab_node_agent_steps()`：**確定性後處理**，依異常訊息/服務標籤偵測服務（nginx/pg/redis/mysql），注入對應 (service, operation) 步驟
   - 服務停止 → `<svc>.restart` / `nginx.start`（轉成 runner spec）
   - **特定** config error 訊號（invalid line、syntax error、can't open config、MySQL `data dir not found` / `invalid datadir`…）→ `<svc>.restore_config`（還原 known-good config）
4. `_to_fixing_plan()`：轉成 schema 3.1 可執行 FixingPlan（runner spec + extractor 驗證 + `execution_profile`）
   - `execution_profile`：由 `runner_catalog.execution_profile_for()` **確定性**從 plan 的 runner spec + 靜態路徑表產生（`profile_version='1.0'`、`generated_by='runner_catalog'`，含 `allowed_commands[]` / `path_permissions[]`），LLM 不參與
   - `pre_execution_snapshot`：**service-aware**，`_snapshot_command_for()` 依服務選 `<svc>.ensure_config_snapshot`
   - `final_verification`：**service-aware**，`_final_verification_for()` 依服務選 `<svc>.connection_test` / `redis.ping` / `nginx.http_check`
5. **Taint 覆寫（ADR-007）**：若工具輸出被 `log_guard` 判定有 injection-shaped 內容，`analyze_cluster` 會在 `_to_fixing_plan` 之後覆寫 `environment_policy` 三個 key——`auto_execute_allowed=False`、`requires_approval=True`、`security_review_required=True`，被污染的 plan 永遠無法自動執行，強制人工在 Gate 審查。
6. Pydantic 驗證後寫入 `diagnosis_reports`

**防注入**：`affected_service` 只能來自觀測標籤；LLM 不能直接送 shell command，只能經 Layer 2 deterministic post-process 轉成 `(service, operation)`，再由 `runner_catalog.py` 產生固定 argv runner spec；日誌一律視為**不可信輸入**並以 `fence()` 圍欄後才餵給 LLM。工具來源標籤為 `loki` / `prometheus` / `diagnostic_command`。

**重要**：若產生不出可執行步驟，會 fallback 成非執行用 schema（`*_fallback` diagnosis_id），Gate 會拒絕審批 → 代表 L2 某處出錯（過去遇過：空 `affected_service`/`root_cause` 觸發 Pydantic 例外）。

### 2.5 Gate：審批閘道（`dashboard/` ｜ container `dashboard` ｜ 跑在 C）

人工審批閘道（Flask + Direction-B 兩欄式 Console UI）。

- **Admin-key 認證**（`X-Admin-API-Key`）：只有 POST mutation 需要；GET 讀取端點不需 admin key
- **approve / reject / execute** 三個動作
- **30 分鐘授權窗口**：approve 後逾時 execute 會被 403 拒絕（見 Chaos CM-05）
- **Idempotency 矩陣**：同 key 重送回 200/400/409
- **Schema guard**：`SUPPORTED_EXECUTION_SCHEMAS = {'3.0', '3.1'}`（向下相容接受兩者；標準產出為 `SUPPORTED_EXECUTION_SCHEMA = '3.1'`）。approve / execute 都檢查 plan schema 在支援集合內且有可執行步驟，否則拒絕。
- **`plan_sha256`（TOCTOU 守門）**：approve 時 Gate 算 `plan_sha256(plan)` 並寫入 `plan_approvals.plan_sha256`，把審批綁定到「當下被核可的確切 plan 內容」。雜湊定義為 `sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')))`，Dashboard 與 Executor 兩份實作必須 **byte-identical**（第三條 parity coupling）。Executor 執行前重算，若不符即 **pause**，drift 記為 `approved_plan_hash_mismatch`（ADR-006）。

#### ADR-005：ExecutionProfile / PolicyCard（fail-closed 權限 manifest）

- FixingPlan 3.1 攜帶一份必填 `execution_profile` manifest，由 `runner_catalog.execution_profile_for` **確定性**產生（`profile_version='1.0'`、`generated_by='runner_catalog'`），LLM 完全不參與。
- 每個 grant 都是 **exact-command grant**（`allow_extra_args=False`）：完整 argv 被釘死，`systemctl is-active nginx` 永遠不會授權 `systemctl stop nginx`，`curl <probe-url>` 也不會授權其他 curl。
- pi-agent 的 `PolicyCard`（`pi-agent/src/safety_cards/policy_card.py`）以此 manifest **fail-closed** 執行；預設 `AADS_POLICY_MODE=enforce`（不在允許清單→deny；`audit` 模式只警告但放行）。`AADS_POLICY_MODE` 未列在 `.env.example` 或 compose，僅以程式碼預設值生效。
- `profile_allows()` 有兩份拷貝（layer2 與 pi-agent 各一），`layer2-analyzer/tests/test_parity.py` 釘住兩者**判定結果一致**（verdict 相同，非逐字相同——docstring 本就不同）。改一份就要改另一份。

#### ADR-006：Drift 偵測與可恢復暫停

- `plan_sha256` 是 TOCTOU 守門（見上）；任何審批後 plan 被改動都會在執行前被擋下。
- `paused_for_review` 是 **非終態**：執行可在人工裁決後 resume 或 abort。
- 暫停事件寫入 `execution_escalations`（migration 005）。`drift_type` 列舉：`approved_plan_hash_mismatch` / `policy_violation` / `verification_failed` / `step_retries_exhausted` / `final_verification_failed` / `unrecoverable_step_state`；`status` = `open` | `resolved`；`resolution` = `resumed` | `aborted` | `rediagnosed`（`rediagnosed` 保留未用）；`severity` 固定 `high`；`escalation_id` 為 `esc_<uuid>`。
- 失敗經 `classify_step_failure`（executor）分類後路由（hook_denied / verification_failed / retries_exhausted → pause/escalate），在 Dashboard 以 **resume / abort** 呈現。

#### ADR-007：日誌注入防護（log_guard）

- `layer2-analyzer/src/log_guard.py` = 確定性 regex scanner + `fence()` 資料圍欄 + contextvars taint registry。
- 所有會吸收外部 log 資料的 agent 工具都被包起來；日誌是餵給 LLM 的**不可信輸入**，必須保持圍欄。
- 一旦偵測到注入訊號，被污染的診斷會在 `analyze_cluster` 中被覆寫成強制人工審查（`auto_execute_allowed=False` / `requires_approval=True` / `security_review_required=True`），永遠無法自動執行。生產環境本來就以 schema 層級禁止 auto-execution（prod 計畫不可 auto-executable）。

### 2.6 Layer 4：Knowledge Agent / Executor（`layer4-executor/` ｜ container `layer4-executor` ｜ 跑在 C）

確定性順序執行器（`src/executor.py`），**無任何 LLM import**（單元測試保證）。透過 HTTP 呼叫 target 上的 On-Device Agent 來實際執行。

**執行流程**：
1. 取得 node lock（TTL = max(timeout×2, 120s)，finally 釋放，啟動時 sweep 過期 lock）
2. **重算 `plan_sha256`** 與審批時的 hash 比對；不符即 pause（`approved_plan_hash_mismatch`，ADR-006 TOCTOU 守門）
3. `validate_plan`：`SUPPORTED_PLAN_SCHEMAS = {'3.0', '3.1'}`；當 `schema_version=='3.1'` 時，`execution_profile.allowed_commands` 為必填，缺則 `missing_execution_profile`
4. `pre_execution_snapshot`：先建立 known-good 快照；無快照且 `on_failure=block` → 阻擋
5. 依序執行每個 step 的 runner spec（per-step `POST /v1/commands/run`）
6. 每步有 verification；失敗經 `classify_step_failure` 分類後決定 rollback / abort / **pause**（hook_denied / verification_failed / retries_exhausted → pause + 寫 `execution_escalations`）
7. `final_verification` 確認整體修復成功
8. 終態寫入 `plan_executions.status`

**終態（`TERMINAL_STATUSES`，`executor.py`）**：
`final_verified`、`kb_imported`、`kb_skipped`（成功但不匯入知識，`ENABLE_KNOWLEDGE_BASE=false` 時為正常成功）、`kb_import_failed`、`execution_failed`、`execution_failed_unknown_state`、`blocked`（被安全規則擋下）。

**非終態**：`paused_for_review`（等人工 resume / abort）與 `failed_retryable`（暫時性失敗，可重試）都**不是**終態。

### 2.7 資料層：TimescaleDB（container `timescaledb` ｜ 跑在 C ｜ 13 張表）

跨所有層的狀態與稽核儲存。Schema 由 `migrations/000`-`005` 定義（000 建 12 張表；005 新增第 13 張表 `execution_escalations`，並為 `plan_approvals` 加上 `plan_sha256` 欄位）。各表大致歸屬：

| 層 | 表 |
|----|-----|
| L0 | `raw_logs` |
| L1 | `anomaly_logs` |
| L2 | `diagnosis_reports`、`knowledge_cases` |
| Gate | `plan_approvals`（含 `plan_sha256`）、`idempotency_records` |
| L4 | `plan_executions`、`execution_steps`、`node_locks`、`execution_escalations`（drift/暫停佇列） |
| 跨層 | `agent_nodes`（節點/runner_capabilities 註冊）、`agent_tasks`、`audit_events`（全程稽核） |

---

## 3. 修復的四層架構模式

每個受支援的服務都遵循同一套模式（這是擴展新服務的範本）：

```
Layer 1 (偵測)      →  Layer 2 (診斷+計畫)      →  pi-agent runner (執行)            →  E2E 測試 (驗證)
Alloy 收集日誌        FixingPlan 3.1 步驟          argv runner + PolicyCard/hook/audit  SR-* 場景
LOKI_QUERY 過濾       service-aware 後處理         root runner + wrapper policy
```

**擴展新服務的清單**（以 PostgreSQL 為例）：
1. 5 個 wrapper：`restart` / `reload` / `config-test` / `ensure-config-snapshot` / `restore-config`
2. `pi-agent/src/main.py`：CommandRunner + Hook pipeline + `/v1/commands/run` + health 檢查
3. sudoers 維持只授權單一 root runner；新增 wrapper 必須 root-owned、不可被 `aads-agent` 修改，並由 Layer 2 runner spec 呼叫
4. systemd unit 的 `ReadWritePaths` 加入該服務的 config 與 log 路徑
5. Layer 2 `_ensure_lab_node_agent_steps` / `_final_verification_for` / `_snapshot_command_for` / `_expected_outcome_for` / `_verification_for` 加入該服務分支
6. Layer 2 system prompt 加入該服務的故障→動作對應
7. Alloy config 加入該服務的 log source
8. SR-* E2E 測試場景

---

## 4. 安全模型重點

| 機制 | 防範的風險 |
|------|-----------|
| argv runner + PolicyCard(fail-closed) + hook/audit + 無任意 shell | LLM 幻覺指令（`AADS_POLICY_MODE=enforce` 預設，profile 外的 argv 直接 deny；ADR-005） |
| ExecutionProfile exact-command grant（`allow_extra_args=False`） | probe 指令被偷換成 mutation（`is-active`→`stop`）；LLM 不參與 profile 產生 |
| `plan_sha256` TOCTOU 守門 | 審批後 plan 被竄改（執行前重算不符即 pause，`approved_plan_hash_mismatch`；ADR-006） |
| `log_guard` taint → 強制人工審查 | 日誌注入操縱 LLM 診斷（被污染的 plan 永不自動執行；ADR-007） |
| root-owned wrapper + 單一 root runner sudoers grant | 提權、wrapper 被竄改、任意 sudo command |
| pre-execution snapshot 強制 | 在沒有回滾基準的狀態下亂動（CM-07 / SR-PG-03） |
| 30 分鐘授權窗口 | 過期授權被利用（CM-05） |
| node lock | 並行修復互相干擾（CM-06） |
| DB-driven 恢復 | executor crash 後重複執行（CM-02） |
| 上游故障靜默降級 | 資料來源斷裂時誤判/誤修（CM-03 / CM-04） |
| 失敗釋放 lock + failed_retryable（非終態，可重試） | network/agent 故障造成 lock 洩漏（CM-01 / CM-08） |

---

## 5. 實驗環境

### 5.1 目前 demo lab（PVE/Tailscale reachable）

> 真實 IP 與 node id 以 `.env.lab` 與當前 lab 拓撲為準，請勿寫進文件或 commit。下方以佔位符表示。

- **Controller**（`${AADS_CONTROLLER_IP}`）：跑所有 Docker 服務（Layer 1/2/4、Gate、Loki、Grafana、Prometheus、TimescaleDB、LiteLLM 等）
- **Target**（`${AADS_TARGET_IP}`）：被監控的目標主機，跑 On-Device Agent + 業務服務（nginx、postgresql、redis、mysql）+ Alloy
- **Dashboard**：`http://${AADS_CONTROLLER_IP}:5000/`
- **Target node id**：`${AADS_TARGET_NODE_ID}`
- **Gate policy**：demo 應維持 `AADS_FORCE_GATE_APPROVAL=true`，讓 operator 手動 Approve / Execute
- **課堂 demo 腳本**：`bash scripts/lab/demo-nginx-manual-gate.sh`，只準備 Nginx bad-config Queue，不代按 Approve / Execute

### 5.2 本機 Multipass lab（bootstrap/runbook 用）

- **aads-controller**（常見 `192.168.252.2`）：跑 controller Docker Compose stack
- **aads-target**（常見 `192.168.252.3`）：Ubuntu 24.04 target node
- 實際 IP 以 `scripts/lab/up.sh` 產生的 `.env.lab` 與 Multipass 狀態為準

**啟動/管理腳本**（`scripts/lab/`）：`up.sh`、`down.sh`、`sync-controller.sh`、`install-target-alloy.sh`、`check-prereqs.sh`

⚠️ **部署注意**：layer2-analyzer 程式碼是 baked 進 Docker image，改動後需 `docker compose build`（force-recreate 不夠）。pi-agent wrapper 需 transfer 到 target 的 `/usr/local/sbin/`。目前使用 `scripts/lab/deploy-controller.sh` / `scripts/lab/deploy-target.sh` 做部署。詳見 [service-coverage-handoff.md](service-coverage-handoff.md) §4。

---

## 6. 目前 Lab 狀態與已知問題

### 2026-06-03 demo lab 入口確認

> 位址以佔位符表示；實際 IP 見 `.env.lab`。

- Dashboard `http://${AADS_CONTROLLER_IP}:5000/` 回 HTTP 200
- Grafana `http://${AADS_CONTROLLER_IP}:3000/` 回 HTTP 200
- Prometheus health `http://${AADS_CONTROLLER_IP}:9090/-/healthy` 回 HTTP 200
- Loki ready `http://${AADS_CONTROLLER_IP}:3100/ready` 回 HTTP 200
- Layer 2 health `http://${AADS_CONTROLLER_IP}:8080/health` 回 HTTP 200
- Target agent `http://${AADS_TARGET_IP}:8090/health` 未帶 token 回 HTTP 401，代表 endpoint 可達且 auth 保護生效

### 2026-06-03 Nginx 手動 Dashboard demo

課堂分享建議使用 `scripts/lab/demo-nginx-manual-gate.sh`：

```bash
bash scripts/lab/demo-nginx-manual-gate.sh
```

這支腳本會驗證 `.env.lab` 的 forced manual approval / node id / Loki query、檢查 Dashboard Admin Key、確認 target runner 是 `runner.v1`，然後拒絕 stale pending/approved items、還原 Nginx baseline、注入壞的 `nginx.conf`、等待新的 schema 3.1 restore plan 出現在 Queue。它不會呼叫 approve 或 execute API。

operator 在 Dashboard 手動按 **Approve** 與 **Execute** 後，可用：

```bash
bash scripts/lab/demo-nginx-manual-gate.sh --verify <DIAGNOSIS_ID>
```

驗證 execution trace、`nginx -t`、`systemctl is-active nginx` 與本機 HTTP health。若 Queue 同時產生兩筆，應選 runner 包含 `aads-nginx-restore-known-good` / `nginx.restore_config` 的 restore plan；`nginx.start` 類型通常是 repair 後延遲 log 造成的 stale item。

### 2026-06-02 手動 Gate demo 後狀態

- controller：`${AADS_CONTROLLER_IP}`；target：`${AADS_TARGET_IP}`
- target `node_id`：`${AADS_TARGET_NODE_ID}`
- 成功完成的人工 Gate 修復：Nginx bad config、PostgreSQL stopped、Redis bad config、MySQL bad config
- 最新確認的 target health：
  - nginx active，`nginx -t` successful
  - PostgreSQL accepting connections
  - Redis `PONG`
  - MySQL active/running；`mysqladmin -u root ping` 回 `Access denied` 表示 server reachable
  - `aads-agent` active

### MySQL 修復鏈的設計修正

MySQL 在 live demo 中暴露了三個重要邊界：

1. **config discovery 必須選 server config**：`/etc/mysql/mysql.conf.d/mysql.cnf` 是 client config，破壞它不一定會讓 daemon 掛掉；wrapper 現在優先使用 `mysqld.cnf` / `50-server.cnf`。
2. **RCA-to-action 分類必須認得 runtime config failure**：`data dir not found` / `invalid datadir` 屬於 config repair，不是單純 restart。
3. **snapshot refresh 只能在健康 baseline 上發生**：`mysql.ensure_config_snapshot` 現在需要 service active、server reachable、config validate 全部成立；service failed 時保留既有 known-good snapshot。

### 目前仍需釐清 / 待處理

- **Docker target coverage**：`aads-target` 尚未安裝 Docker，所以 `SR-DC-01` / `SR-DC-02` 仍 SKIP。
- **live-only lab config**：target Alloy multi-service log source 與 `.env.lab` 的 `LAYER1_LOKI_QUERY` 仍需要沉澱成 repo template。
- **Gate stale Queue**：成功修復後，延遲 log window 可能再產生同服務的 pending item。短期以 target health + 最新 execution 判斷，長期應加入 diagnosis dedupe/cooldown。
- **Layer 2 diagnostics 邊界**：LLM agent 的 diagnostic shell 目前跑在 analyzer container，常見 host tools（`journalctl`、`ps`、`ss`、`docker`、`curl`）可能不存在；可靠修復仍應依 target-side runner probes。

---

## 7. 相關文件

- **測試說明**：[AADS-Testing.md](AADS-Testing.md) — Chaos E2E 與 Service-Coverage E2E 完整場景
- **Codex 交接**：[service-coverage-handoff.md](service-coverage-handoff.md) — 服務擴展現況與待辦
