# AADS 測試說明文件

**文件版本**: 1.2 ｜ **更新日期**: 2026-06-17 ｜ **對應分支**: `feature/execution-hardening`（HEAD `942bd55`）

本文件涵蓋 AADS 的兩套端對端測試：**Chaos E2E**（測系統自身韌性）與
**Service-Coverage E2E**（測修復能力）。系統架構見 [AADS-System-Overview.md](AADS-System-Overview.md)。

---

## 0. Demo 入口與腳本

目前 demo 的 Dashboard / Gate Console：

```text
http://100.72.172.83:5000/
```

demo lab 常用環境變數：

```bash
export AADS_CONTROLLER_IP=100.72.172.83
export AADS_TARGET_IP=100.77.197.118
export AADS_SSH_USER=bs10081
```

手動 UI demo 的建議入口是新的 Nginx 單場腳本。它會做部署後 demo 的現場準備與故障注入，但刻意停在 Dashboard Queue 產生後，保留 **Approve** 與 **Execute** 給 operator 手動操作：

```bash
bash scripts/lab/demo-nginx-manual-gate.sh
```

腳本會檢查：
- `.env.lab` 必須維持 `AADS_FORCE_GATE_APPROVAL=true`
- `AADS_NODE_ID` / `AADS_DEFAULT_NODE_ID` 必須是 `3bf76430-cf5d-44c6-8ff2-ee0161f73740`
- `LAYER1_LOKI_QUERY` 必須是 `{node_id="3bf76430-cf5d-44c6-8ff2-ee0161f73740"}`
- target `/v1/node/facts` 必須回 `runner_capabilities.schema_version="runner.v1"`，且不能再有 legacy `supported_commands`

當腳本印出 recommended `diagnosis_id` 後，請開 Dashboard 手動按 **Approve**，再按 **Execute**。執行後用同一支腳本查 execution trace 與 Nginx health：

```bash
bash scripts/lab/demo-nginx-manual-gate.sh --verify <DIAGNOSIS_ID>
```

成功終態接受 `kb_skipped`、`final_verified`、`kb_imported`；目前 lab `ENABLE_KNOWLEDGE_BASE=false` 時 `kb_skipped` 是正常成功。

Service-Coverage 腳本會自動注入故障、等待 plan、呼叫 Gate API approve/execute，適合用來展示「指令列一鍵驗證」：

```bash
bash scripts/lab/e2e-service-repair.sh              # 全部服務修復場景
bash scripts/lab/e2e-service-repair.sh SR-PG-01     # 單一場景
bash scripts/lab/e2e-service-repair.sh --service mysql
```

如果 demo 目標是展示 Dashboard 上的人工 Gate 流程，請不要用 `e2e-service-repair.sh` 直接跑完整場景，因為它會透過 API 自動 approve/execute。請改用 `scripts/lab/demo-nginx-manual-gate.sh`。

---

## 1. 兩套測試的差異

| 維度 | Chaos E2E | Service-Coverage E2E |
|------|-----------|---------------------|
| 腳本 | `scripts/lab/chaos-e2e.sh` | `scripts/lab/e2e-service-repair.sh`；手動 UI demo 用 `scripts/lab/demo-nginx-manual-gate.sh` |
| 場景前綴 | `CM-*` | `SR-*` |
| 目的 | AADS **自身**掛掉還能不能活？ | AADS 能不能**修好它監控的服務**？ |
| 故障對象 | AADS 元件（agent、executor、Loki、LiteLLM…） | 業務服務（nginx、postgresql、redis…） |
| 驗證問題 | 安全性、狀態一致性、不洩漏 lock | 偵測 → 計畫 → 執行 → 服務恢復 |
| 現況 | ✅ 8/8 全 PASS | ✅ 已安裝服務 7/7 PASS；Docker 2 項因未安裝 SKIP |

兩者互補：一個確保系統壞掉時「安全停下」，一個確保服務壞掉時「能修起來」。

---

## 2. Chaos E2E（CM-01 ~ CM-08）— 全數通過

靈感來自 Netflix Chaos Monkey：在 pipeline 每一層主動注入故障，驗證狀態機一致性、
node lock 生命週期、優雅降級、恢復正確性。

### 執行方式

```bash
./scripts/lab/chaos-e2e.sh              # 全部場景（安全相依順序）
./scripts/lab/chaos-e2e.sh CM-05        # 單一場景
./scripts/lab/chaos-e2e.sh --random 3   # 隨機抽 3 個
./scripts/lab/chaos-e2e.sh --seed 42 --random 5   # 可重現的隨機（同 seed = 同結果）
```

執行順序（`chaos_schedule`）：gate 測試 → container-kill → 破壞性測試，
用 Fisher-Yates + seeded RANDOM 確保 CI 可重播失敗。

### 場景與驗證的安全屬性

| 場景 | 注入的故障 | 預期結果 | 驗證的屬性 |
|------|-----------|---------|-----------|
| **CM-01** agent_killed_mid_execution | execute 前殺掉 target 的 `aads-agent` | `failed_retryable`，node_locks=0 | Agent 死亡不洩漏 lock |
| **CM-02** executor_crashed_mid_step | 執行中途 stop layer4-executor 容器再重啟 | 從 DB 恢復 → `kb_skipped` | crash 後從 DB（非記憶體）恢復、不重複執行 |
| **CM-03** loki_unavailable | 停 Loki 90 秒並弄壞 nginx | 期間無新 anomaly 寫入 | 資料來源斷裂不誤報 |
| **CM-04** litellm_down | 停 LiteLLM 並弄壞 nginx | 期間無 schema 3.1 plan 產生 | LLM 不可用時不亂發計畫 |
| **CM-05** approval_expired_before_exec | approve 後改 DB 讓授權過期再 execute | HTTP 403 拒絕 | 授權時間窗口硬性執行 |
| **CM-06** concurrent_repair_lock | 注入一個 live node_lock 模擬並行 | `policy.blocked:node_locked` | 同節點最多一個執行中 |
| **CM-07** snapshot_missing | 執行前刪掉 nginx 快照 | `blocked:snapshot_failed` | 無回滾基準時拒絕修復 |
| **CM-08** network_partition | iptables 封鎖 :8090 | `failed_retryable`，node_locks=0 | 網路分割安全降級、不洩漏 lock |

### 最終結果

```
PASS: 8   FAIL: 0   SKIP: 0
```

### 開發過程中修掉的關鍵 bug

| Bug | 根因 | 修法 |
|-----|------|------|
| `sql()` 無限 hang | `multipass exec` SSH slot 被並行 hung 連線耗盡 | `sql()` 加 `timeout 30` |
| CM-06 一直 SKIP | SQL 查了不存在的欄位 `target_node_id`（應為 `node_id`） | 改正欄位名 |
| CM-01 偶發 FAIL | `sleep 3` 後殺 agent 是 race，快的 VM 已執行完 | 改成 execute **前**就殺 agent |
| CM-07 一直 FAIL | `ensure_known_good_snapshot` 在無快照時會用當前（壞）狀態重建 | 移除 `mkdir -p`，先 guard pre-existing 快照 |
| CM-07 偶發 no plan | 緊接在 CM-04（LiteLLM 重啟）後，LLM 還沒 warm-up | 加 60 秒 pipeline readiness 等待 |

---

## 3. Service-Coverage E2E（SR-*）

驗證 AADS 對它監控的服務的偵測與修復能力。

### 執行方式

```bash
./scripts/lab/e2e-service-repair.sh              # 全部場景
./scripts/lab/e2e-service-repair.sh SR-PG-01     # 單一場景
./scripts/lab/e2e-service-repair.sh --service pg # 只跑 PostgreSQL（pg/redis/docker/mysql）
```

VM 使用部署副本，測試前請先同步/部署最新程式：

```bash
bash scripts/lab/deploy-controller.sh
AADS_CONTROLLER_SERVICE=layer1-filter bash scripts/lab/deploy-controller.sh
bash scripts/lab/deploy-target.sh
```

> 長時間執行請用 `nohup`（Bash 工具有 10 分鐘上限）：
> `nohup bash scripts/lab/e2e-service-repair.sh > /tmp/sr.log 2>&1 &`

### 場景設計

每個「修復」場景的驗證流程：注入故障 → 等 Layer 1/2 產生 plan → approve → execute → 確認服務恢復 active。

| 場景 | 注入的故障 | 預期結果 |
|------|-----------|---------|
| **SR-PG-01** postgresql_stopped | `systemctl stop postgresql` | AADS → `postgresql.restart` → 服務 active |
| **SR-PG-02** postgresql_bad_config | 注入 config 語法錯誤 | AADS → `restore_known_good_config` → 服務 active |
| **SR-PG-03** postgresql_no_snapshot | 刪除快照後停 PG | 執行 `blocked:snapshot_failed`（保護未知基準） |
| **SR-RD-01** redis_stopped | `systemctl stop redis-server` | AADS → `redis.restart` → 服務 active |
| **SR-RD-02** redis_bad_config | 注入 redis.conf 錯誤 | AADS → `restore_known_good_config` → 服務 active |
| **SR-DC-01** container_stopped | `docker stop <allowlisted>` | AADS → `docker.container_restart` → running |
| **SR-DC-02** container_not_allowed | 對已移除的 legacy catalog endpoint `POST /v1/actions/run` 發請求 | pi-agent 回 **404/405**（catalog endpoint 已在 runner.v1/V2 移除；非白名單容器的拒絕現在發生在 wrapper，回 `blocked: container_not_allowed`） |
| **SR-MY-01** mysql_stopped | `systemctl stop mysql` | AADS → `mysql.restart` → 服務 active |
| **SR-MY-02** mysql_bad_config | 注入 my.cnf 錯誤 | AADS → `restore_known_good_config` → 服務 active |

服務未安裝時場景自動 SKIP（不算 FAIL）。

### 安全特性：Docker 容器白名單（單層 wrapper 管制）

`docker.container_restart` / `docker.container_start` 的容器名管制現在是**單一層**：
root-owned wrapper 腳本（`pi-agent/wrappers/aads-docker-container-restart`、
`aads-docker-container-start`）讀取 `AADS_DOCKER_ALLOWED_CONTAINERS`（comma-separated，
設在 target 的 `/etc/aads-agent/agent.env`），不在清單內就 `echo "blocked: container_not_allowed"`
並 `exit 42`。

runner.v1/V2 模型已移除舊的 catalog `arg_allowlist`（不再有靜態指令目錄當安全邊界），
所以過去描述的「catalog + wrapper 雙層」不再成立。sudoers 仍用 `*` 通配參數，
但實際容器名管制只在 wrapper。

> 注意：wrapper 內第 11 行註解仍寫「second gate after pi-agent catalog validation」，
> 那是 V2 前的殘留文字；catalog gate 已不存在，wrapper 即唯一的容器白名單關卡。

---

## 4. Service-Coverage 目前狀態

### 最終完整執行（2026-06-02）

```
SR-PG-01  PASS  terminal=kb_skipped, postgresql=active
SR-PG-02  PASS  terminal=kb_skipped, postgresql=active
SR-PG-03  PASS  blocked:snapshot_failed
SR-RD-01  PASS  terminal=kb_skipped, redis=active
SR-RD-02  PASS  terminal=kb_skipped, redis=active
SR-DC-01  SKIP  Docker not installed on aads-target
SR-DC-02  SKIP  Docker not installed on aads-target
SR-MY-01  PASS  terminal=kb_skipped, mysql=active
SR-MY-02  PASS  terminal=kb_skipped, mysql=active

PASS: 7   FAIL: 0   SKIP: 2
```

完整 log：`/tmp/aads-service-repair-final-20260602T115235.log`

Docker 兩個場景不是失敗，而是 target VM 尚未安裝 Docker；安裝 Docker 並建立
`aads-test-nginx` allowlisted container 後，預期完整目標為 `PASS: 9 FAIL: 0 SKIP: 0`。

最後 target health：

```
postgresql: /var/run/postgresql:5432 - accepting connections
redis: PONG
mysql: Access denied for user 'root'@'localhost'（server reachable）
aads-agent: active
```

`kb_skipped` 在目前 lab 是成功終態，因為 `ENABLE_KNOWLEDGE_BASE=false`。

### 手動 Gate 修復 Demo（2026-06-02；2026-06-03 補一鍵 Nginx demo 腳本）

這輪不是重跑自動 PASS 報表，而是從乾淨 Gate 開始，讓 operator 在 UI 手動
Approve / Execute，驗證實際修復閉環：target service 真的被破壞，Layer 1/2
產生 FixingPlan 3.1，Gate 出現 Queue，Layer 4 執行後服務恢復。

> Schema 版本說明：Layer 2 **只**產生 FixingPlan **3.1**（`schema_version`
> 在 `layer2-analyzer/src/schemas/action_plan.py` 被 pin 為 `Literal["3.1"]`，
> `execution_profile` 為必填，model 直接拒絕 3.0）。3.0 僅在**消費端**作為 legacy-accept
> 保留：executor 的 `validate_plan`（`layer4-executor/src/executor.py`）與 dashboard
> 的 `SUPPORTED_EXECUTION_SCHEMAS` 接受 `{3.0, 3.1}`，但 `execution_profile.allowed_commands`
> 只在 schema==3.1 時要求。本 lab demo 看到的都是 3.1。

2026-06-03 起，課堂展示建議使用 `scripts/lab/demo-nginx-manual-gate.sh` 產生單一
Nginx bad-config 場景。它會自動拒絕 stale pending/approved queue items、還原 Nginx baseline、
注入壞設定、等待 schema 3.1 restore plan，但不會 approve/execute。若 Queue 產生兩筆，
選 runner 包含 `aads-nginx-restore-known-good` / `nginx.restore_config` 的那筆；延遲 log
產生的 `nginx.start` item 視為 stale，不應執行。

Gate-only reset 只清 Gate 顯示來源與執行狀態：

```sql
DELETE FROM node_locks;
DELETE FROM diagnosis_reports;
```

`diagnosis_reports` 的外鍵 cascade 會清掉相關 `plan_approvals`、
`plan_executions`、`execution_steps`、`plan_execute` idempotency records。
`raw_logs`、`anomaly_logs`、`audit_events` 保留，方便追查證據。

| Demo | 故障注入 | Gate plan | Expected command | Execution | Terminal | 修復驗證 |
|------|----------|-----------|------------------|-----------|----------|----------|
| Nginx bad config | `nginx.conf` invalid directive；`nginx -t` fail，HTTP 舊 worker 仍可能 200 | `diag_cluster_0_1780388144` | `nginx.restore_known_good_config` | `exec_bf8fdb55b7e24a96b2359f8400ce9bfa` | `kb_skipped` | `nginx -t` pass、service active、HTTP 200 |
| PostgreSQL stopped | `systemctl stop postgresql`；`pg_isready` no response | `diag_cluster_0_1780388401` | `postgresql.restart` | `exec_5d6c0a63a5244ddb9ce32d4bd541d746` | `kb_skipped` | `postgresql.service` / `postgresql@16-main` active，`pg_isready` accepting |
| Redis bad config | `redis.conf` invalid directive；restart fail、`redis-cli ping` refused | `diag_cluster_0_1780388700` | `redis.restore_known_good_config` | `exec_d545741acc924aee9eff2f343cba09fa` | `kb_skipped` | `redis-server.service` active，`redis-cli ping` = `PONG` |
| MySQL bad config | `mysqld.cnf` invalid `datadir=/nonexistent/...`；restart fail、socket unreachable | `diag_cluster_0_1780390317` | `mysql.restore_known_good_config` | `exec_9585cf92a913455a9b023d492a246e81` | `kb_skipped` | `mysql.service` active/running；bad marker 0；`mysqladmin` = `Access denied`（server reachable） |

MySQL demo 的最終 execution trace：

```text
step 0  mysql.ensure_config_snapshot      -> step_verified
        snapshot_refreshed=false existing_snapshot=true service_active=false
step 1  mysql.restore_known_good_config   -> step_verified
        config_restored=true config_file=/etc/mysql/mysql.conf.d/mysqld.cnf
final   mysql.connection_test             -> success, accepting_connections=true
```

這輪手動 demo 額外驗證了一件自動報表不容易暴露的事：pre-execution snapshot
不能在服務已故障時刷新 known-good snapshot，否則會把壞設定保存成回滾基準。

### 開發過程中已修掉的 bug（依發現順序）

| # | 問題 | 根因 | 修法（commit） |
|---|------|------|---------------|
| 1 | PG/Redis/MySQL 永遠 no plan | Layer 1 `LOKI_QUERY` 寫死 `{source="target-nginx"}`；Alloy 不 tail 這些服務的 log | 改 query 為 `{node_id=...}`、Alloy 加 log source（`d981d29`） |
| 2 | 非 nginx 服務產生空步驟 plan | `_ensure_lab_node_agent_steps` 只認 nginx | 擴展到 pg/redis/mysql 關鍵字（`d981d29`） |
| 3 | Layer 2 崩潰成 fallback plan | 空 `affected_service`/`root_cause` 觸發 Pydantic min_length | 加 fallback 預設值（`a8e05a9`） |
| 4 | 所有修復 blocked: Read-only file system | systemd `ProtectSystem=strict`，`ReadWritePaths` 只有 `/etc/nginx` | 加入 pg/redis/mysql 的 config 與 log 路徑（`c50d22d`） |
| 5 | restore 後服務讀不到 config | 快照 restore 後 ownership 變 `root:aads-agent` | restore 後 chown 回服務原生 owner（`5a097d9`） |
| 6 | Redis 修復成功卻 execution_failed | `final_verification` 寫死 `nginx.http_check` | service-aware `_final_verification_for`（`da9ad94`） |
| 7 | SR-PG-03 沒正確 block | `pre_execution_snapshot` 寫死 nginx 快照指令 | service-aware `_snapshot_command_for`（`da9ad94`） |
| 8 | 停止服務被誤判為 config error | `is_config_error` 比對到泛用的 "error"/"failed" | 收緊成特定 config 失敗訊號（`da9ad94`） |
| 9 | PG restore 永遠 invalid | `postgres -t` 語法錯且 binary 不在 PATH；`pg_ctlcluster` 無 configtest action | 改用 restart + is-active 當驗證（`da9ad94`） |
| 10 | SR-RD-02 no plan generated | Layer 1 forward query 超過 `BATCH_SIZE` 時把 cursor 推到 poll end，跳過後段 Redis config error log | cursor 改推到 newest fetched log timestamp，新增 `test_cursor_timestamp.py` |
| 11 | SR-PG-03 後 target PG 殘留 stopped | no-snapshot guard PASS 後只重建 snapshot，沒有 reset baseline | `restore_pg_after_snapshot_guard` 重建 snapshot 並重啟 PostgreSQL |
| 12 | MySQL bad-config demo 沒真的打壞服務 | wrapper / lab helper 用排序第一個 `.cnf`，選到 client config `/etc/mysql/mysql.conf.d/mysql.cnf` | MySQL config discovery 優先選 server config：`mysqld.cnf` / `50-server.cnf` |
| 13 | MySQL invalid datadir 被規劃成 `mysql.restart` | Layer 2 config signature 未涵蓋 `invalid datadir` / `data dir not found` / option-file parse 訊號 | 擴充 deterministic config-error signatures，移除錯誤的 `mysql.restart` execute step |
| 14 | MySQL restore 被 snapshot 污染擋住 | `mysql.ensure_config_snapshot` 在 service failed 時仍因 `mysqld --validate-config` 回 0 而刷新壞 snapshot | snapshot refresh 必須同時滿足 service active + `mysqladmin` reachable + config validate |

### 剩餘待辦（優先序）

1. **Docker 場景** — 在 target 裝 Docker + 建 `aads-test-nginx` 容器
2. **target 設定進 git** — Alloy config、`.env.lab` 的 `LAYER1_LOKI_QUERY` 目前仍是 live-only
3. **Layer 1 lab image 輕量化** — 若語意過濾未啟用，避免預設 rebuild 仍下載 torch/transformers
4. **Gate duplicate / stale plan handling** — repair 成功後，延遲 log window 仍可能再產生一筆同服務 `pending_approval`（例如 MySQL post-recovery `mysql.restart`）。操作上先以 target health 和最新 successful execution 判斷，不應直接 execute stale item；後續可加入 diagnosis dedupe / cooldown。
5. **Layer 2 diagnostic command boundary** — LLM agent 的 `execute_diagnostic_command` 目前在 analyzer container 內跑，常遇到 `journalctl`、`ps`、`ss`、`docker`、`curl` 不存在。deterministic post-process 可補足修復步驟，但 RCA 工具邊界仍需釐清：要改成 target-side probes，或明確限制為 controller-local diagnostics。

詳細交接見 [service-coverage-handoff.md](service-coverage-handoff.md)。

---

## 4.5 Per-service 單元測試

除了上面兩套 lab E2E，每個 service 目錄各自帶 pytest 單元測試。慣例：每個測試檔
用 `sys.path.insert(0, ".../src")` 把該 service 的 `src/` 推進 path，所以 **pytest
必須從 service 目錄內執行**（從 repo root 直接對某個 service 跑通常可行，但對 import
路徑最安全的方式仍是進到該 service 目錄）。

```bash
# 無額外相依，repo 內 .venv 即可跑：
.venv/bin/python -m pytest pi-agent/tests/ -q
.venv/bin/python -m pytest layer4-executor/tests/ -q
.venv/bin/python -m pytest layer1-filter/tests/ -q

# Layer 2 需要完整 agent stack（langchain/langgraph，見 layer2-analyzer/requirements.txt），
# 從它自己的目錄跑：
cd layer2-analyzer && /…/AADS/.venv/bin/python -m pytest tests/ -q
```

**取得當下測試數量**（不要硬背數字，會隨開發漂移；用 collect-only 確認）：

```bash
# 從 service 目錄內：
cd pi-agent && python -m pytest --co -q
```

在 HEAD `942bd55`（branch `feature/execution-hardening`）上以 `--co -q` 收集到的數量：

| Service | Collected tests | 備註 |
|---------|-----------------|------|
| `pi-agent` | 35 | 無額外相依 |
| `layer4-executor` | 37 | 無額外相依 |
| `layer1-filter` | 4 | 無額外相依 |
| `layer2-analyzer` | 104 | 在精簡 `.venv`（缺 heavy agent stack）上會有 1 個 collection error（`tests/test_agent_tools.py`）；完整 stack 下應全綠 |
| `dashboard` | 0 | 目前無測試 |

> 這些是 **collected**（收集到）的數量，不等於 PASS 數量。實際 PASS/FAIL 請各自跑一次。
> 完整綠燈需在 Docker/CI 的真實 stack 下執行。

### Execution-hardening 對應的測試覆蓋（ADR-005/006/007）

執行路徑硬化引入了一批新的非終態與防護，對應測試與不變量如下：

| 防護 / 狀態 | 來源 | 覆蓋 / 驗證方式 |
|------------|------|----------------|
| **`profile_allows` 雙副本一致性（ADR-005）** | `layer2-analyzer/src/schemas/action_plan.py` 與 `pi-agent/src/safety_cards/policy_card.py` 各有一份 | `layer2-analyzer/tests/test_parity.py` 斷言兩份 `profile_allows` 對同一輸入回**相同 verdict**（`l2_result == pi_result`），不是 byte-identical 原始碼（docstring 本來就不同） |
| **ExecutionProfile 由程式產生** | `runner_catalog.execution_profile_for`（`layer2-analyzer/src/runner_catalog.py`）；LLM 不參與 | profile manifest：`profile_version='1.0'`、`generated_by='runner_catalog'`、`allowed_commands[]`、`path_permissions[]`，且為 exact-command 授權（`allow_extra_args=False`） |
| **PolicyCard fail-closed** | `pi-agent/src/safety_cards/policy_card.py`，`AADS_POLICY_MODE` 預設 `enforce`（deny），`audit` 為 warn-but-run | PolicyCard 已 ship 且預設 fail-closed；`AADS_POLICY_MODE` 不在 `.env.example`／compose，僅程式預設 |
| **`plan_sha256` drift（TOCTOU，ADR-006）** | dashboard 與 executor 各有一份 `sha256(json.dumps(plan, sort_keys=True, separators=(',',':')))`，必須 byte-identical（第三組 parity coupling） | hash mismatch → 暫停，`drift_type='approved_plan_hash_mismatch'` |
| **`paused_for_review` 為非終態** | `layer4-executor/src/executor.py`，`TERMINAL_STATUSES` = `{final_verified, kb_imported, kb_skipped, kb_import_failed, execution_failed, execution_failed_unknown_state, blocked}` | `paused_for_review`、`failed_retryable` 皆**非**終態；`kb_skipped` 在 `ENABLE_KNOWLEDGE_BASE=false` 時是正常成功 |
| **`classify_step_failure` 路由** | `layer4-executor/src/executor.py::classify_step_failure` | hook_denied / verification_failed / retries_exhausted → pause/escalate，於 dashboard 呈現為 resume/abort |
| **`execution_escalations`（migration 005）** | `layer0-storage/timescaledb/migrations/005_execution_escalations.sql`（第 13 張表 + `plan_approvals.plan_sha256` 欄位） | `drift_type` enum：`approved_plan_hash_mismatch \| policy_violation \| verification_failed \| step_retries_exhausted \| final_verification_failed \| unrecoverable_step_state`；`status`：`open \| resolved`；`resolution`：`resumed \| aborted \| rediagnosed`（`rediagnosed` 目前保留未用）；`severity` 寫死 `high`；`escalation_id` = `esc_<uuid>` |
| **log-injection 防禦（ADR-007）** | `layer2-analyzer/src/log_guard.py`（deterministic regex scanner + `fence()` + contextvars taint registry） | tainted diagnosis 會在 `analyze_cluster`（`main.py`）於 `_to_fixing_plan` 之後覆寫三個 key：`auto_execute_allowed=False`、`requires_approval=True`、`security_review_required=True`，tainted plan 永遠無法 auto-execute；prod auto-execution 本身即 schema-forbidden |

---

## 5. 常用除錯指令

```bash
# 查某次執行為何 blocked / failed：
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -At -c \
  \"SELECT result::text FROM plan_executions WHERE plan_id='<PLAN>' ORDER BY requested_at DESC LIMIT 1;\""

# 確認 plan 是否為可審批的 schema 3.1（空/404 = fallback = Layer 2 出錯）：
curl -s -H \"X-Admin-API-Key: $(tr -d '\n' < .aads-lab-admin-key)\" \
  -X POST http://${AADS_CONTROLLER_IP}:5000/api/plans/<PLAN>/approve \
  -H 'Content-Type: application/json' -d '{\"reason\":\"x\"}'

# Layer 2 崩潰日誌：
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  logs --tail=30 layer2-analyzer | grep -E 'ERROR|Exception|Pydantic'"

# 確認 runner capabilities 已上線（on-device pi-agent 監聽 8090）：
curl -s -H \"Authorization: Bearer <token>\" http://${AADS_TARGET_IP}:8090/v1/node/facts | \
  python3 -c \"import sys,json; print(json.load(sys.stdin)['runner_capabilities'])\"
```

**Gotcha**：測試腳本的 `sql()` 有 `timeout 30`。不要在背景發可能 hang 的 DB 查詢，
會耗盡 `multipass exec` 的 SSH slot 並阻塞後續呼叫。
