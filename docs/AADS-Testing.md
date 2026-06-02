# AADS 測試說明文件

**文件版本**: 1.0 ｜ **更新日期**: 2026-06-02 ｜ **對應分支**: `codex/ubuntu-agent-layer0-4`

本文件涵蓋 AADS 的兩套端對端測試：**Chaos E2E**（測系統自身韌性）與
**Service-Coverage E2E**（測修復能力）。系統架構見 [AADS-System-Overview.md](AADS-System-Overview.md)。

---

## 1. 兩套測試的差異

| 維度 | Chaos E2E | Service-Coverage E2E |
|------|-----------|---------------------|
| 腳本 | `scripts/lab/chaos-e2e.sh` | `scripts/lab/e2e-service-repair.sh` |
| 場景前綴 | `CM-*` | `SR-*` |
| 目的 | AADS **自身**掛掉還能不能活？ | AADS 能不能**修好它監控的服務**？ |
| 故障對象 | AADS 元件（agent、executor、Loki、LiteLLM…） | 業務服務（nginx、postgresql、redis…） |
| 驗證問題 | 安全性、狀態一致性、不洩漏 lock | 偵測 → 計畫 → 執行 → 服務恢復 |
| 現況 | ✅ 8/8 全 PASS | 🚧 進行中（見 §4） |

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
| **CM-04** litellm_down | 停 LiteLLM 並弄壞 nginx | 期間無 schema 2.0 plan 產生 | LLM 不可用時不亂發計畫 |
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
| **SR-DC-02** container_not_allowed | 嘗試重啟非白名單容器 | pi-agent 回 400 `container_not_allowed` |
| **SR-MY-01** mysql_stopped | `systemctl stop mysql` | AADS → `mysql.restart` → 服務 active |
| **SR-MY-02** mysql_bad_config | 注入 my.cnf 錯誤 | AADS → `restore_known_good_config` → 服務 active |

服務未安裝時場景自動 SKIP（不算 FAIL）。

### 安全特性：Docker 容器雙重白名單

`docker.container_restart` 的容器名受**兩層**白名單管制：
1. **pi-agent catalog** 的 `arg_allowlist`（HTTP 層，回 400）
2. **wrapper 腳本**內的 `AADS_DOCKER_ALLOWED_CONTAINERS` 環境變數（OS 層）

兩層缺一不可，sudoers 用 `*` 通配參數但實際管制在 wrapper。

---

## 4. Service-Coverage 目前進度與已知問題

### 最近一次完整執行（在最終修復 commit `da9ad94` **部署前**）

```
SR-PG-01  FAIL  blocked / restored_config_invalid
SR-PG-02  FAIL  blocked
SR-PG-03  FAIL  blocked but reason 為空
SR-RD-01  FAIL  execution_failed（但 redis 已 active — 修復其實成功）
SR-RD-02  FAIL  execution_failed（同上）
SR-DC-01/02  SKIP  Docker 未安裝
SR-MY-01/02  SKIP  MySQL 偵測 flaky
```

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

### 待辦（優先序）

1. **部署 `da9ad94` 後重跑** — 預期 SR-PG-01/02/03、SR-RD-01/02 翻 PASS
2. **測試隔離** — 場景共用 Loki lookback 視窗，前一場景的壞 config log 會污染下一場景的 cluster
3. **MySQL 安裝穩定化** — `mysql_installed()` 偵測 flaky
4. **Docker 場景** — 在 target 裝 Docker + 建 `aads-test-nginx` 容器
5. **target 設定進 git** — Alloy config、`.env.lab` 的 `LOKI_QUERY` 目前只在線上
6. **部署自動化** — 寫 `deploy-target.sh` / `deploy-controller.sh`

詳細交接見 [service-coverage-handoff.md](service-coverage-handoff.md)。

---

## 5. 常用除錯指令

```bash
# 查某次執行為何 blocked / failed：
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  exec -T timescaledb psql -U logdb -d logdb -At -c \
  \"SELECT result::text FROM plan_executions WHERE plan_id='<PLAN>' ORDER BY requested_at DESC LIMIT 1;\""

# 確認 plan 是否為可審批的 schema 2.0（空/404 = fallback = Layer 2 出錯）：
curl -s -H \"X-Admin-API-Key: $(tr -d '\n' < .aads-lab-admin-key)\" \
  -X POST http://192.168.252.2:5000/api/plans/<PLAN>/approve \
  -H 'Content-Type: application/json' -d '{\"reason\":\"x\"}'

# Layer 2 崩潰日誌：
multipass exec aads-controller -- bash -lc "cd ~/AADS && sudo docker compose --env-file .env.lab \
  logs --tail=30 layer2-analyzer | grep -E 'ERROR|Exception|Pydantic'"

# 確認 catalog 指令已上線：
curl -s -H \"Authorization: Bearer <token>\" http://192.168.252.3:8090/v1/node/facts | \
  python3 -c \"import sys,json; print(len(json.load(sys.stdin)['supported_commands']), 'commands')\"
```

**Gotcha**：測試腳本的 `sql()` 有 `timeout 30`。不要在背景發可能 hang 的 DB 查詢，
會耗盡 `multipass exec` 的 SSH slot 並阻塞後續呼叫。
