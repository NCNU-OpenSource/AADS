# AADS Lab Deploy & E2E Validation Plan

> **執行者**：CoreX（自動執行）  
> **目標**：在 PVE 環境部署 AADS 並驗證 V2 Command Runner 端到端正常
> **分支**：`codex/ubuntu-agent-layer0-4`

---

## 環境資訊

| 角色 | IP | SSH User |
|------|----|----------|
| **Controller**（Docker 服務） | `100.72.172.83` | `bs10081` |
| **Target**（On-Device Agent） | `100.77.197.118` | `bs10081` |

Working directory（本機）：`/Users/bs10081/Developer/AADS`

---

## 前置確認

```bash
# 確認兩台機器 SSH 可達
ssh -o StrictHostKeyChecking=no bs10081@100.72.172.83 'echo "ctrl ok: $(hostname)"'
ssh -o StrictHostKeyChecking=no bs10081@100.77.197.118 'echo "target ok: $(hostname)"'
```

如果其中一台不通，**停止**並回報錯誤。

---

## Phase 1：Target — 部署 On-Device Agent V2

### 1a. 確認 target 基礎環境

```bash
ssh bs10081@100.77.197.118 '
  python3 --version &&
  systemctl is-active nginx 2>/dev/null || echo "nginx not running" &&
  id aads-agent 2>/dev/null || echo "aads-agent user missing"
'
```

如果 python3 不存在，先安裝：
```bash
ssh bs10081@100.77.197.118 'sudo apt-get update -qq && sudo apt-get install -y python3 python3-venv nginx uuid-runtime curl'
```

### 1b. 打包並上傳 pi-agent

```bash
cd /Users/bs10081/Developer/AADS

COPYFILE_DISABLE=1 tar --exclude='._*' -C . -czf /tmp/aads-pi-agent.tgz pi-agent

scp -o StrictHostKeyChecking=no /tmp/aads-pi-agent.tgz bs10081@100.77.197.118:/tmp/
ssh bs10081@100.77.197.118 '
  rm -rf /tmp/aads-pi-agent &&
  mkdir -p /tmp/aads-pi-agent &&
  tar -xzf /tmp/aads-pi-agent.tgz -C /tmp/aads-pi-agent &&
  rm /tmp/aads-pi-agent.tgz
'
```

### 1c. 取得 agent token

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)
echo "TOKEN length: ${#TOKEN}"  # 應該是 64 chars
```

### 1d. 執行 install.sh（以 sudo 跑）

```bash
ssh bs10081@100.77.197.118 \
  "sudo env AADS_AGENT_TOKEN='${TOKEN}' AADS_AGENT_ENVIRONMENT=test \
   bash /tmp/aads-pi-agent/pi-agent/install/install.sh"
```

**預期輸出**：
- `visudo -cf /etc/sudoers.d/aads-agent` 通過
- `aads-agent.service` active
- 最後一行 `Active: active (running)`

**常見問題**：
- `missing root runner wrapper` → `aads-root-command-runner` 沒被打包；確認 `pi-agent/wrappers/aads-root-command-runner` 存在
- `AADS_NGINX_SNAPSHOT_DIR missing nginx snapshot` → 正常，首次執行 install.sh 會用當前 nginx config 建立 snapshot

### 1e. 確認 agent facts

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)
curl -fsS -H "Authorization: Bearer ${TOKEN}" \
  http://100.77.197.118:8090/v1/node/facts | python3 -m json.tool
```

**預期回應**（V2 schema）：
```json
{
  "node_id": "...",
  "environment": "test",
  "agent_version": "2.0.0",
  "runner_capabilities": {
    "schema_version": "runner.v1",
    "modes": ["argv"],
    "supports_as_root": true,
    "hook_default": "allow_audit"
  }
}
```

⚠️ 如果看到 `supported_commands` 而非 `runner_capabilities`，代表安裝的是舊版 main.py，需要重新執行 1b-1d。

### 1f. 紀錄 node_id

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)
NODE_ID=$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
  http://100.77.197.118:8090/v1/node/facts | python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])')
echo "NODE_ID=${NODE_ID}"
```

---

## Phase 2：Controller — 部署 Docker 服務

### 2a. 確認 controller 有 Docker

```bash
ssh bs10081@100.72.172.83 'docker --version && docker compose version'
```

如果沒有：
```bash
ssh bs10081@100.72.172.83 '
  sudo apt-get update -qq &&
  sudo apt-get install -y docker.io docker-compose-v2 &&
  sudo usermod -aG docker bs10081
'
# 重新登入使 group 生效
```

### 2b. 同步 repo 到 controller

```bash
cd /Users/bs10081/Developer/AADS
AADS_CONTROLLER_IP=100.72.172.83 AADS_SSH_USER=bs10081 \
  scripts/lab/sync-controller.sh
```

**預期**：`Synced to controller 100.72.172.83`

### 2c. 更新 .env.lab

> `.env.lab` 已存在於本機（有 TOKEN、ADMIN_KEY、LLM keys）。  
> 需要把 NODE_ID 更新成新 target 的 node_id，並確認 IP 設定。

```bash
# NODE_ID 取自 Phase 1f
NODE_ID=$(curl -fsS \
  -H "Authorization: Bearer $(tr -d '\n' < .aads-lab-token)" \
  http://100.77.197.118:8090/v1/node/facts | python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])')

# 更新 .env.lab 中的 NODE_ID
cd /Users/bs10081/Developer/AADS
sed -i.bak \
  -e "s/^AADS_NODE_ID=.*/AADS_NODE_ID=${NODE_ID}/" \
  -e "s/^AADS_DEFAULT_NODE_ID=.*/AADS_DEFAULT_NODE_ID=${NODE_ID}/" \
  .env.lab
echo "Updated NODE_ID to ${NODE_ID}"
```

確認 .env.lab 關鍵值存在（不看敏感值）：
```bash
grep -E "^(PI_AGENT_TOKEN|AADS_ADMIN_API_KEY|AADS_NODE_ID|AADS_DEFAULT_NODE_ID|AADS_LITELLM_UPSTREAM_API_KEY|LITELLM_MODEL)=" .env.lab | sed 's/=.*/=<set>/'
```

### 2d. 把 .env.lab 上傳到 controller

```bash
scp -o StrictHostKeyChecking=no \
  /Users/bs10081/Developer/AADS/.env.lab \
  bs10081@100.72.172.83:~/AADS/.env.lab
```

### 2e. 啟動所有服務

```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS &&
  COMPOSE_BAKE=false sudo -E docker compose --env-file .env.lab up -d --build
'
```

**等待約 2-3 分鐘**，然後確認服務狀態：

```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS &&
  sudo docker compose --env-file .env.lab ps --format "table {{.Name}}\t{{.Status}}"
'
```

**所有 container 應該是 `Up` 狀態**。重要服務清單：
- `timescaledb`
- `layer2-analyzer`
- `layer4-executor`
- `dashboard`
- `litellm`
- `layer1-filter`

如果有 container Exiting，查看 log：
```bash
ssh bs10081@100.72.172.83 'cd ~/AADS && sudo docker compose --env-file .env.lab logs --tail=30 <container_name>'
```

### 2f. 確認 DB migration

```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS &&
  sudo docker compose --env-file .env.lab exec -T timescaledb \
    psql -U logdb -d logdb -At -c "SELECT table_name FROM information_schema.tables WHERE table_schema='"'"'public'"'"' ORDER BY table_name;"
'
```

**應該看到**：`agent_nodes`、`audit_events`、`diagnosis_reports`、`execution_steps`、`node_locks`、`plan_executions` 等 12 張表。

---

## Phase 3：註冊 Target 節點

```bash
ADMIN_KEY=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-admin-key)
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)
NODE_ID=$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
  http://100.77.197.118:8090/v1/node/facts | python3 -c 'import json,sys; print(json.load(sys.stdin)["node_id"])')
RUNNER_CAPS=$(curl -fsS -H "Authorization: Bearer ${TOKEN}" \
  http://100.77.197.118:8090/v1/node/facts | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["runner_capabilities"]))')

PAYLOAD=$(python3 -c "
import json
print(json.dumps({
  'node_id': '${NODE_ID}',
  'environment': 'test',
  'agent_version': '2.0.0',
  'base_url': 'http://100.77.197.118:8090',
  'runner_capabilities': json.loads('${RUNNER_CAPS}'),
}))
")

curl -fsS \
  -H "X-Admin-API-Key: ${ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d "${PAYLOAD}" \
  http://100.72.172.83:5000/api/agents/register
```

**預期回應**：`{"status": "registered", "node_id": "..."}`

確認節點出現在 dashboard：
```bash
ADMIN_KEY=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-admin-key)
curl -fsS -H "X-Admin-API-Key: ${ADMIN_KEY}" \
  http://100.72.172.83:5000/api/agents | python3 -m json.tool
```

---

## Phase 4：V2 Runner 功能驗證

### 4a. 直接測試 /v1/commands/run

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)

# read probe: systemctl is-active nginx
curl -fsS \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "schema_version": "runner.v1",
    "runner": {"argv": ["systemctl", "is-active", "nginx"], "side_effect": "read"},
    "context": {"service": "nginx", "operation": "status", "purpose": "verify"}
  }' \
  http://100.77.197.118:8090/v1/commands/run | python3 -m json.tool
```

**預期**：`status: success`、`stdout: active`、`hook.decision: allow`、`hook.card_id: audit.allow_all.v1`

```bash
# 確認舊的 catalog endpoint 已移除
curl -s -o /dev/null -w "%{http_code}" \
  -X POST http://100.77.197.118:8090/v1/actions/run \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"command_id":"nginx.status"}'
# 應回 404 或 405（endpoint 不存在）
```

### 4b. 確認 /health 檢查

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)
curl -fsS -H "Authorization: Bearer ${TOKEN}" \
  http://100.77.197.118:8090/health | python3 -m json.tool
```

**預期**：`{"status": "healthy", ...}`

如果 unhealthy，看 problems 清單：
- `missing root runner wrapper` → 重新執行 Phase 1b-1d
- `sudoers does not allow root runner` → `ssh bs10081@100.77.197.118 'sudo cat /etc/sudoers.d/aads-agent'` 確認只有 root-command-runner 一行

---

## Phase 5：E2E 服務修復測試

### 5a. Nginx bad config 修復流程

```bash
TOKEN=$(tr -d '\n' < /Users/bs10081/Developer/AADS/.aads-lab-token)

# 記錄修復前 execution count
BEFORE=$(ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
  psql -U logdb -d logdb -At -c "SELECT count(*) FROM plan_executions;"
' 2>/dev/null)
echo "Before: ${BEFORE} executions"

# 注入 bad config
AADS_TARGET_IP=100.77.197.118 AADS_SSH_USER=bs10081 \
  /Users/bs10081/Developer/AADS/scripts/lab/break-nginx-bad-config.sh

echo "Bad config injected. Waiting for AADS to detect and repair (up to 5 min)..."
```

監控 Layer 2 log：
```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS &&
  sudo docker compose --env-file .env.lab logs -f --tail=20 layer2-analyzer
' &
LOG_PID=$!
sleep 300
kill $LOG_PID 2>/dev/null || true
```

確認修復：
```bash
# 1. plan_executions 有新紀錄且 status = final_verified 或 kb_imported
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
  psql -U logdb -d logdb -At -c "
    SELECT execution_id, status, result->'"'"'steps'"'"'->0->'"'"'status'"'"' as step_status
    FROM plan_executions
    ORDER BY requested_at DESC LIMIT 3;"
'

# 2. nginx 應恢復正常
ssh bs10081@100.77.197.118 'systemctl is-active nginx && nginx -t'

# 3. execution_steps 有 service=nginx, operation=restore_config 且 step_verified
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
  psql -U logdb -d logdb -At -c "
    SELECT step_id, command_id, status
    FROM execution_steps
    ORDER BY id DESC LIMIT 5;"
'
# command_id 欄位應顯示 "nginx.restore_config"（V2 label）
```

### 5b. MySQL bad config 修復（如果 MySQL 有安裝）

```bash
# 確認 MySQL 是否存在
ssh bs10081@100.77.197.118 'systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null || echo "mysql not installed"'
```

如果已安裝：
```bash
# 注入 bad config（同 e2e-service-repair.sh 的 SR-MY-02 邏輯）
ssh bs10081@100.77.197.118 sudo bash -lc '
  CONF=$(find /etc/mysql -name "mysqld.cnf" -o -name "50-server.cnf" 2>/dev/null | head -1)
  [[ -n "$CONF" ]] || CONF=/etc/mysql/my.cnf
  echo "chaos_option = invalid_chaos_mysql" >> "$CONF"
  systemctl stop mysql 2>/dev/null || systemctl stop mariadb 2>/dev/null
'
echo "MySQL bad config injected"
```

等待修復（約 5 分鐘），然後確認：
```bash
ssh bs10081@100.77.197.118 'systemctl is-active mysql 2>/dev/null || systemctl is-active mariadb 2>/dev/null'
# 期望: active
```

---

## Phase 6：Audit Trail 驗證

```bash
# 確認 hook decision 有被記錄在 audit_events
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
  psql -U logdb -d logdb -At -c "
    SELECT event_type, policy_decision, result, time
    FROM audit_events
    WHERE event_type LIKE '"'"'execution.%'"'"'
    ORDER BY time DESC LIMIT 10;"
'
```

**預期**：看到 `execution.attempt`、`execution.command_success`、`execution.final_verified` 等事件，`policy_decision = allowed`。

---

## Pass/Fail 標準

| 項目 | Pass 條件 |
|------|-----------|
| Phase 1e | `runner_capabilities.schema_version = "runner.v1"` |
| Phase 1e | **沒有** `supported_commands` 欄位 |
| Phase 2f | 12 張表全部存在 |
| Phase 3 | `{"status": "registered"}` |
| Phase 4a | `hook.decision = "allow"`、`hook.card_id = "audit.allow_all.v1"` |
| Phase 4a | `/v1/actions/run` 回 404/405 |
| Phase 4b | `{"status": "healthy"}` |
| Phase 5a | `plan_executions.status = final_verified 或 kb_imported` |
| Phase 5a | `execution_steps.command_id = "nginx.restore_config"` |
| Phase 5a | `nginx` 修復後 active |
| Phase 6 | `audit_events` 有 execution.* 事件 |

---

## 常見問題 Quick Ref

**agent health 顯示 `missing node id file`**
```bash
ssh bs10081@100.77.197.118 'sudo ls -la /etc/aads-agent/'
# 如果缺少 node-id，重跑 install.sh 或手動建立
ssh bs10081@100.77.197.118 'sudo bash -c "uuidgen > /etc/aads-agent/node-id && chown root:root /etc/aads-agent/node-id && chmod 644 /etc/aads-agent/node-id"'
```

**agent service 啟動失敗**
```bash
ssh bs10081@100.77.197.118 'sudo journalctl -u aads-agent -n 40 --no-pager'
```

**layer2-analyzer 沒有產生 FixingPlan**
```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab logs --tail=50 layer2-analyzer
' | grep -E "ERROR|FixingPlan|schema_version"
# 如果看到 schema validation error，代表 action_plan.py 沒有被正確打包進 image
# 需要重新 build: sudo docker compose --env-file .env.lab build layer2-analyzer
```

**executor 拒絕 plan（unsupported_schema）**
```bash
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab logs --tail=50 layer4-executor
' | grep -E "unsupported_schema|schema_version"
# 應該是 3.0；如果看到 2.0 被拒，代表 executor.py 沒有被打包進 image
```

**Dashboard 無法顯示 runner details**
```bash
# 確認 SUPPORTED_EXECUTION_SCHEMA 是 3.0
ssh bs10081@100.72.172.83 '
  cd ~/AADS && sudo docker compose --env-file .env.lab exec -T timescaledb \
  psql -U logdb -d logdb -At -c "
    SELECT diagnosis_id, action_plan->'"'"'schema_version'"'"' as schema
    FROM diagnosis_reports ORDER BY created_at DESC LIMIT 5;"
'
```
