# AADS On-Device Agent V2: Command Runner + Hook Pipeline 取代 Catalog

> ⚠️ **HISTORICAL DESIGN DOC（已實作並被取代/擴充）。** 本文記錄的 runner V2 設計
> **已全部落地**，但其安全模型隨後由 **execution-hardening** 三條 ADR 取代/擴充：
> FixingPlan schema 升到 **3.1**（必帶 `execution_profile`），on-device hook 鏈改為
> `[PolicyCard(), AuditHook()]` 且 **PolicyCard 預設 fail-closed enforce**。本文許多細節
> （schema `3.0`、AuditHook allow-all、`audit.allow_all.v1` 作為決策卡）在 HEAD `942bd55`
> 已過時——權威來源請以程式碼與下列 ADR 為準，本文僅作歷史脈絡：
> [ADR-005](obsidian-vault/ADR/ADR-005-execution-profile-and-policy-card.md) ·
> [ADR-006](obsidian-vault/ADR/ADR-006-drift-detection-pause-escalate.md) ·
> [ADR-007](obsidian-vault/ADR/ADR-007-log-injection-defense.md)。
>
> Status: **shipped & superseded** · Schema bump: FixingPlan `2.0 → 3.0`（現為 **3.1**） · Agent API: `runner.v1`
> 取代對象: Catalog Command Whitelist（`pi-agent` `CATALOG` + `/v1/probes/run` + `/v1/actions/run` + facts `supported_commands`）

## 1. 目標與安全邊界轉移

把 on-device agent 從「static `command_id` whitelist」改成「argv-first full-power command runner + context-aware hook」。

安全邊界不再是「`command_id` 是否存在於 catalog」，而是每一次 execution 的：

- 完整 **runner spec**（argv / cwd / env / timeout / as_root / side_effect）
- 完整 **context**（purpose / service / operation / plan_id / step_id / evidence_refs）
- **hook / safety card 決策**（V2 初版只有 AuditHook=allow；**現況**：`HOOKS=[PolicyCard(), AuditHook()]`，PolicyCard 先判，預設 fail-closed `enforce` → 違反 ExecutionProfile 即 `deny`）
- **append-only audit**（argv hash、decision、context、result 全部入庫）

### 已鎖定決策

1. **直接替換 catalog**，不做長期 adapter。舊 catalog 流程在程式碼以註解標記為 legacy/removal note。
2. **argv-first**；v1 不開 shell string。未來需要 shell 另開顯式 `mode=shell`。
3. **explicit root**：預設以 agent 使用者執行；`as_root=true` 才走單一 root runner wrapper。
4. ~~**hook 預設 allow + audit**：v1 沒有 safety card 時允許執行但完整記錄。~~
   **（已被取代）** 現況：hook 鏈為 `[PolicyCard(), AuditHook()]`，PolicyCard 預設
   `AADS_POLICY_MODE=enforce`（fail-closed），違反 plan 內 `execution_profile` 即 `deny`；
   AuditHook 仍永遠 allow 但只負責完整審計。詳見
   [ADR-005](obsidian-vault/ADR/ADR-005-execution-profile-and-policy-card.md)。

## 2. 與 V1 plan 的差異（本次 review 補強）

V1 plan 把 catalog 當成「純白名單」，但 catalog 同時承載了 Layer 4 的**行為元資料**
（`scope` / `idempotent` / `retry_policy` / `timeout_seconds`），這些驅動了 lock / retry /
snapshot / rollback / verification。直接拿掉而不補對應欄位，會打斷三條正常流程。V2 補上：

| Gap（V1 會壞的地方） | V2 補法 |
|---|---|
| **Verification 結構化輸出消失**：catalog probe 原本回 `active/http_status/pong` 等結構化欄位；純 argv 只回 `{returncode,stdout,stderr}`，`matches_expected` 無欄位可比，且違反 `free_text_verification_not_allowed`。 | `VerificationSpec` 新增**宣告式 `extract`**：把 stdout/stderr/returncode 解析成結構化 observed 欄位，再與 `expected` 比對。argv-first 且不違反 free-text 規則。 |
| **mutation 訊號消失**：lock / snapshot 觸發原本靠 `scope=="action"`；`as_root != mutating`。 | `RunnerSpec` 新增 **`side_effect: read \| mutate`**。Layer 4 用它決定上不上 node lock、要不要 snapshot。**不**用 `as_root` 推斷。 |
| **rollback 寫死 `nginx.restore_known_good_config`**：command_id 消失後所有服務 rollback 壞。 | `FixingPlan` 新增 **`rollback.runner`**，由 plan 帶下來，服務通用。 |
| **能力預檢 gate 歸零**：`validate_supported_commands` 失效，LLM 幻覺 argv 直接落地。 | V2 初版由 AuditHook（allow+audit）承接（trade-off，見 §6）；**現況已由 `PolicyCard` 補回**——plan 隨附 `execution_profile`（schema 3.1 必帶），逐 request 對照 `allowed_commands` / `path_permissions`，預設 fail-closed `enforce`（[ADR-005](obsidian-vault/ADR/ADR-005-execution-profile-and-policy-card.md)）。 |
| **dashboard 註冊 `supported_commands`** 欄位失效。 | 改存 `runner_capabilities`；前端顯示一併遷移。 |

## 3. Schema 3.0（`layer2-analyzer/src/schemas/action_plan.py`）

> ⚠️ **本節描述的 3.0 已被 3.1 取代。** 在 HEAD `942bd55`，`action_plan.py` 把
> `schema_version` pin 成 `Literal["3.1"]`，並新增 **必填** `execution_profile` 欄位
> （deterministic manifest，`profile_version="1.0"`、`generated_by="runner_catalog"`、
> `allowed_commands[]` / `path_permissions[]`，由 `runner_catalog.execution_profile_for`
> 產生，LLM 不參與）。Layer 2 **只**輸出 3.1，model 直接拒絕 3.0。3.0 僅在
> **consumer 端 legacy-accept**：executor `SUPPORTED_PLAN_SCHEMAS={"3.0","3.1"}`、
> dashboard `SUPPORTED_EXECUTION_SCHEMAS=frozenset(['3.0','3.1'])`；其中
> `execution_profile.allowed_commands` 的覆蓋驗證只在 `schema==3.1` 強制。
> 詳見 [ADR-005](obsidian-vault/ADR/ADR-005-execution-profile-and-policy-card.md)。下文保留為歷史。

### RunnerSpec
```jsonc
{
  "mode": "argv",                       // v1 只支援 argv
  "argv": ["/usr/local/sbin/aads-mysql-restore-config"],
  "cwd": "/",
  "env": {},
  "timeout_seconds": 60,
  "as_root": true,                      // true → 走 root runner wrapper
  "side_effect": "mutate",              // read | mutate（驅動 lock / snapshot）
  "stdin": null
}
```

### ExtractRule（verification 用）
每個 key 從一段輸出產出一個 observed 欄位：
```jsonc
"extract": {
  "active":   { "from": "stdout_stripped", "equals": "active" },   // → bool
  "http_code":{ "from": "stdout_stripped", "as_int": true },        // → int
  "returncode": { "from": "returncode" }                            // → int
}
```
- `from ∈ {stdout, stderr, stdout_stripped, stderr_stripped, returncode}`（信任邊界，僅此五種）
- op：`equals`/`contains`/`regex`（→bool）、`as_int`（→int）、`json_path`（→value）、預設 raw（→stripped string）

### VerificationSpec
```jsonc
{
  "type": "runner_probe",
  "runner": { "...": "side_effect 必須為 read" },
  "extract": { "active": { "from": "stdout_stripped", "equals": "active" } },
  "expected": { "active": true }        // 結構化；禁止 text/prompt/llm_judge
}
```

### FixingPlanStep
```jsonc
{
  "step_id": 1, "order": 1,
  "runner": { "...": "side_effect=mutate" },
  "context": { "purpose": "repair", "service": "mysql", "operation": "restore_config" },
  "idempotency": { "mode": "idempotent", "max_attempts": 2 },
  "expected_outcome": "...",
  "on_failure": "rollback",             // abort | rollback
  "verification": { "...VerificationSpec..." }
}
```

### FixingPlan 3.0 → 3.1（現況）
- ~~`schema_version: "3.0"`~~ → **`schema_version: "3.1"`**（`Literal["3.1"]`，Layer 2 只產 3.1）
- **`execution_profile`（必填，3.1 新增）**：deterministic permission manifest，由
  `runner_catalog.execution_profile_for` 產生；逐 runner 涵蓋驗證，PolicyCard 逐 request 強制。
- `pre_execution_snapshot.runner`（取代 `command_id`）
- `rollback`: `{ "enabled": true, "runner": {...mutate...} }`（**新增**）
- `final_verification`: VerificationSpec
- 其餘（plan_id / target_node_id / environment_policy / self_check）不變

舊的 `command_id`-based `FixingPlan 2.0` schema 保留在檔案中標記 `LEGACY`，executor 不再接受。
3.0 不再被 Layer 2 產生，僅 executor / dashboard 以 legacy-accept 方式相容。

## 4. On-Device Agent（`pi-agent/src/main.py`）

- 新增 `CommandRunner`：`subprocess.run(argv, shell=False)`，回 `{returncode, stdout, stderr, duration_ms, timed_out}`。
- `as_root=true` → `["sudo","-n","/usr/local/sbin/aads-root-command-runner"]`，把 argv/cwd/env/timeout 以 **JSON 從 stdin** 傳給 root runner；root runner 仍以 argv（`shell=False`）執行，不接受 shell string。sudoers 只授權這一個 wrapper。
- Hook pipeline：`before_run` → execute → `after_run` / `on_error`。`HookDecision = {decision, card_id, reason, annotations}`。
  **現況**：`HOOKS = [PolicyCard(), AuditHook()]`（`pi-agent/src/main.py:126`）——`PolicyCard` 先判
  （違反 plan 內 `execution_profile` 時 `enforce` 模式回 `deny`、`audit` 模式回 `warn` 但仍執行），
  `AuditHook` 永遠 allow 只負責審計（其 `card_id="audit.allow_all.v1"` 僅是 audit 卡標籤，**不是**安全決策卡）。
  decision 與 argv hash 寫進 response 供 Layer 4 入審計。`pi-agent/src/safety_cards/` 已實作 `PolicyCard`。
- `GET /v1/node/facts` 的 `runner_capabilities` 額外廣播 `hook_default: "policy_card+audit"` 與
  `policy_mode`（= `PolicyCard.POLICY_MODE`，預設 `enforce`），讓 Layer 4 / dashboard 看得到強制狀態。
- **API 變更**：
  - `GET /v1/node/facts` → 回 `runner_capabilities`（取代 `supported_commands`）。
  - **移除** `/v1/probes/run`、`/v1/actions/run`；**新增** `POST /v1/commands/run`。
  - `/health`：改檢查 root runner wrapper 存在 / root-owned / sudoers 授權（不再 iterate catalog wrapper map）。
  - `/v1/agent-tasks/run` 維持不變。
- 服務 wrapper 腳本（`aads-mysql-restore-config` 等）保留為 operational helper，但只當作 argv target 被呼叫，不再是 catalog metadata。

### `/v1/commands/run` request / response
```jsonc
// request
{ "schema_version": "runner.v1", "runner": {...}, "context": {...}, "idempotency": {...} }
// response（hook.decision 由 PolicyCard 決定；deny 時不執行 argv）
{ "status": "success|failed|timeout|blocked", "returncode": 0, "stdout": "...", "stderr": "...",
  "duration_ms": 12, "hook": { "decision": "allow|deny|warn", "card_id": "policy.execution_profile.v1",
  "argv_sha256": "...", "as_root": true, "side_effect": "mutate" }, "retryable": false }
```

## 5. Layer 4 Executor（`layer4-executor/src/executor.py`）

- ~~`SUPPORTED_PLAN_SCHEMA = "3.0"`~~ → 現況 `SUPPORTED_PLAN_SCHEMA = "3.1"` 且
  `SUPPORTED_PLAN_SCHEMAS = {"3.0", "3.1"}`（legacy-accept 3.0；`executor.py:46-47`）。
- 移除 `find_command` / `validate_supported_commands` / catalog 依賴與 facts `supported_commands`。
- `call_agent` 改打 `/v1/commands/run`，body = `{runner, context, idempotency}`。
- **mutation 判斷**：`step.runner.side_effect == "mutate"` → 取 node lock。snapshot 觸發同理。
- **retry**：`max_attempts` 取自 `step.idempotency`（`mode != idempotent` → 1 次），不再看 catalog。
- **verification**：跑 runner（side_effect 必須 read）→ `apply_extractors(raw, extract)` 產 observed → `matches_expected(observed, expected)`。比對失敗時 raw stdout/stderr 寫入 audit。
- **snapshot**：跑 `pre_execution_snapshot.runner`。
- **rollback**：跑 `plan.rollback.runner`（服務通用，取代寫死 nginx）。
- **recovery**：用 `step.idempotency.mode` 取代 catalog `idempotent`。
- `validate_plan`（`executor.py:656`）：3.0/3.1 欄位、`runner.argv` 非空、
  verification.runner.side_effect==read、expected 結構化且無 free-text；當 `schema==3.1` 時
  另要求 `execution_profile.allowed_commands` 涵蓋計畫內所有 runner。
- **Drift / TOCTOU 守門（ADR-006，新增）**：`plan_sha256` = `sha256(json.dumps(plan, sort_keys=True, separators=(',',':')))`
  （`executor.py:738`，與 `dashboard/app.py:30` 必須 byte-identical），與核准時的 hash 不符即
  pause、`drift_type='approved_plan_hash_mismatch'`，落 `execution_escalations`。失敗經
  `classify_step_failure`（`executor.py:744`）路由為 pause/escalate，dashboard 提供 Resume/Abort。

## 6. 安全 trade-off（顯式記錄）

> **Status 2026-06-11：此 trade-off 已關閉。** PolicyCard（ADR-005）、drift 偵測（ADR-006）、log injection 防護（ADR-007）已全部實作並測試。原先遺留的安全缺口詳列如下；各項解法見對應 ADR。

V1→V2 是一次**OS 層防線的刻意降級**：

- **舊**：sudoers 授權 23 個 **exact wrapper 路徑、不帶參數**。agent 被攻陷也只能跑這 23 個。
- **新**：sudoers 只授權單一 `aads-root-command-runner`，wrapper 本身 full-power（任意 argv as root）。
  OS 層不再約束 argv；防線由 **app 層 hook + audit** 承接。

原本的三個遺留缺口及現狀：

| 缺口 | 原狀（V2 初版） | 現狀（feature/execution-hardening） |
|---|---|---|
| **執行範圍無界** | `AuditHook` allow-all；LLM 幻覺 argv 直接以 root 執行 | `PolicyCard` fail-closed（`enforce`）；`ExecutionProfile` 隨 plan 審核、逐 request 強制，見 [ADR-005](obsidian-vault/ADR/ADR-005-execution-profile-and-policy-card.md) |
| **Drift 無法通知人工** | 失敗只有終態 abort/rollback；無 pause→通知→resume 路徑 | `plan_sha256` TOCTOU 守門 + 非終態 `paused_for_review` + `execution_escalations` + dashboard Resume/Abort UI，見 [ADR-006](obsidian-vault/ADR/ADR-006-drift-detection-pause-escalate.md) |
| **Log 污染 prompt injection** | 原始 log 直接進 LLM；唯一防線是 prompt 文字 | `log_guard` 外部 regex 偵測 + data fence + taint registry 強制人工審查（taint 後 `auto_execute_allowed=False` / `requires_approval=True` / `security_review_required=True`），見 [ADR-007](obsidian-vault/ADR/ADR-007-log-injection-defense.md) |

## 7. Tests

> 舊版 `test_catalog.py` 已移除（改 `test_runner.py`）。execution-hardening 另加
> `test_policy_card.py` / `test_drift.py` / `test_parity.py` / `test_log_guard.py`。
> 測試數量會變動，**不要硬記數字**——以 `pytest --co -q` 從各 service 目錄即時取得：
> `pi-agent`、`layer4-executor`、`layer2-analyzer/tests`（layer2 需完整 agent stack，
> 降級 .venv 會有 1 個 collection error）。

- pi-agent：`test_runner.py`、`test_policy_card.py`
  - argv runner 不啟用 shell（`["echo","$HOME"]` 不展開）。
  - `as_root=false` 不走 sudo；`as_root=true` 只呼叫 root runner wrapper。
  - hook order：before → execute → after；`deny` 時不執行。
  - `PolicyCard`：`execution_profile` 涵蓋的 argv → allow；超出 → `enforce` deny / `audit` warn。
- Layer 4：`test_executor_policy.py`、`test_drift.py`
  - 3.1（及 legacy 3.0）plan 接受 runner specs、拒絕缺 `runner.argv`。
  - mutating（`side_effect=mutate`）取得 node lock；retry 用 `idempotency.max_attempts`。
  - `apply_extractors` 正確產出結構化欄位；壞服務不被誤判 verified。
  - drift：`plan_sha256` 不符 → pause + `execution_escalations`（非終態 `paused_for_review`）。
- Layer 2：`test_claude_style_plan.py` / `test_lab_node_agent_steps.py` / `test_parity.py` / `test_log_guard.py`
  - nginx / PostgreSQL / Redis / MySQL repair plan 輸出 runner specs（含 verification.extract）。
  - MySQL bad config → restore wrapper argv 且 `as_root=true`、`side_effect=mutate`。
  - `test_parity.py`：layer2 與 pi-agent 兩份 `profile_allows()` 對相同輸入回傳**相同 verdict**
    （`l2_result == pi_result`，非 byte-identical source）。
  - `test_log_guard.py`：regex scanner / `fence()` / taint registry（ADR-007）。

## 8. Lab 驗證（人工）

- 部署 controller + target。
- 跑 Nginx bad config 與 MySQL bad config 的 Gate flow。
- 確認 target 健康：nginx active/config OK、MySQL active/running、bad marker 移除。
- 重跑 PostgreSQL/Redis/MySQL service-coverage 子集。

## 9. Implementation status（已落地）

採**乾淨切割**：`command_id` 全面移除，display plan / dashboard / 前端改用 `(service, operation)`。

| 區塊 | 檔案 | 變更 |
|---|---|---|
| Schema 3.0→3.1 | `layer2-analyzer/src/schemas/action_plan.py` | 新增 `RunnerSpec` / `ExtractRule` / `RollbackSpec`；`VerificationSpec` 改 runner+extract；`FixingPlanStep` 改 runner+context+idempotency；`FixingPlan` 升 3.0（**現 pin `Literal["3.1"]`、必帶 `execution_profile`**）；`StepCommand` 移除 `command_id`、加 `operation`；新增 `profile_allows()`（與 pi-agent parity） |
| Runner 對照 | `layer2-analyzer/src/runner_catalog.py`（新） | `(service, operation)` → RunnerSpec / VerificationSpec / snapshot / rollback |
| Layer 2 | `layer2-analyzer/src/main.py` | `_ensure_lab_node_agent_steps` 改 operation；`_to_fixing_plan` 輸出 runner specs；storage dump `by_alias=True` |
| On-Device Agent | `pi-agent/src/main.py` | `CommandRunner` + hook 鏈 `[PolicyCard(), AuditHook()]` + `/v1/commands/run` + `runner_capabilities`（含 `hook_default` / `policy_mode`）；移除 catalog |
| PolicyCard | `pi-agent/src/safety_cards/policy_card.py`（新） | fail-closed `enforce` 強制 `execution_profile`；`profile_allows()`（與 layer2 parity）；`AADS_POLICY_MODE` 預設 `enforce`（僅 code default，未列入 `.env.example` / compose） |
| Root runner | `pi-agent/wrappers/aads-root-command-runner`（新） | 讀 stdin JSON、`exec` argv（shell=False） |
| Layer 4 | `layer4-executor/src/executor.py` | schema `{3.0, 3.1}`；`run_runner_command`；`apply_extractors`；side_effect 鎖；rollback.runner；`plan_sha256` drift 守門；`classify_step_failure` + `paused_for_review` |
| Dashboard | `dashboard/app.py` + `static/app/*` | `runner_capabilities`；前端改 runner label；schema gate `frozenset(['3.0','3.1'])`；`plan_sha256`（與 executor byte-identical）；Resume/Abort UI |
| DB | `layer0-storage/.../000_init_tables.sql` + `004_runner_capabilities.sql` + `005_execution_escalations.sql`（新） | `supported_commands` → `runner_capabilities`；005 新增第 13 張表 `execution_escalations` + `plan_approvals.plan_sha256` 欄位（共 6 個 migration 000–005） |
| Install | `pi-agent/install/install.sh`、`scripts/lab/deploy-target.sh` | 裝 root runner + 全 wrapper；sudoers 收斂成單一 root runner grant |

**單元測試**：runner V2 初版的測試之後被 execution-hardening 擴充（新增
`test_policy_card.py` / `test_drift.py` / `test_parity.py` / `test_log_guard.py`）。
測試數量請以 `pytest --co -q` 從各 service 目錄即時統計，勿沿用本文舊數字。
Lab 驗證（§8）仍待人工在 Ubuntu target 上執行。
