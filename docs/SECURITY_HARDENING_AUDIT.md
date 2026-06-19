# AADS 防禦性安全架構審計報告

**範圍**：container boundary、自動化分析→修復 pipeline 的權限設計、pi-agent systemd unit、Docker Compose 配置。
**性質**：防禦性架構審計，目標為系統加固。聚焦設計缺陷、配置不當、邊界防護不足；不含 CVE、不含 PoC。
**審計日期**：2026-06-12
**審計對象**：`docker-compose.yaml`、`docker-compose.prod.yaml`、`pi-agent/`、`layer4-executor/`、`dashboard/`、各服務 Dockerfile。

---

## 摘要

AADS 的整體權限模型有一條清晰且危險的主軸：**被監控主機上的日誌內容（攻擊者可控）→ Layer 1/2 自動分析 → LLM 產生 FixingPlan → Layer 4 以 root 在目標機執行**。V2 刻意移除了 OS 層的指令白名單（per-wrapper sudoers），把整個安全邊界壓到單一個應用層的 `PolicyCard`（ExecutionProfile 比對）上。這個設計在「enforce 模式 + 人工核可」都正常時尚可接受，但有數個單點即可瓦解整條邊界（一個環境變數、一個預設密鑰、一個自動修復開關）。

最關鍵的三個架構弱點：

1. **單一環境變數 `AADS_POLICY_MODE=audit` 即關閉唯一的執行期安全邊界**——所有 policy 違規從 `deny` 降級為 `warn`，root 指令照常執行。
2. **`privileged: true` 的 cadvisor 與掛載 docker.sock 的 alloy** 讓多個觀測容器具備等同 host root 的逃逸能力，`:ro` 對 docker.sock 並不能阻止寫入型 API 呼叫。
3. **間接 prompt injection 影響操作選擇**：目標機日誌是攻擊者可控輸入，流入 LLM RCA 並生成可執行計畫。ExecutionProfile 由 catalog 確定性產生（LLM 不簽發白名單，見 §2.1 更正），故 injection 無法寫出任意 argv；殘餘風險是 LLM 被誘導選出 **catalog 合法卻過廣**的破壞性操作集合。`log_guard.py`（ADR-007）對被污染計畫強制 `security_review_required` 並禁止自動執行，生產自動執行更於 schema 層被禁止（action_plan.py:323-324）。剩餘防線為 PolicyCard 與人工核可。

以下依任務四大面向逐項說明，每項皆附可落地的加固建議。

---

## 1. Container boundary / sandbox escape

### 1.1（嚴重）cadvisor 以 `privileged: true` 執行

`docker-compose.yaml` 與 `prod` 皆設定 cadvisor `privileged: true`，並掛載 `/:/rootfs:ro`、`/var/run:/var/run:ro`、`/dev/disk`、device `/dev/kmsg`。`privileged` 會授予全部 Linux capabilities、解除 seccomp/AppArmor、開放所有 host 裝置節點。即便 rootfs 是 `:ro`，特權容器仍可透過裝置節點、kernel 介面等途徑取得 host root，是典型的 sandbox escape 起點。

**加固建議**：移除 `privileged: true`，改用 cadvisor 官方建議的最小集合：

```yaml
cadvisor:
  cap_drop: ["ALL"]
  cap_add: ["DAC_READ_SEARCH", "SYS_PTRACE"]   # 視實測需要再增補
  security_opt: ["no-new-privileges:true"]
  devices: ["/dev/kmsg:/dev/kmsg:r"]
  read_only: true
  # 維持唯讀掛載，但不要 privileged
```

部署後比對 metrics 是否完整，逐步收斂 capabilities。

### 1.2（嚴重）alloy 掛載 Docker socket，`:ro` 無法阻止寫入

alloy 掛載 `/var/run/docker.sock:/var/run/docker.sock:ro`。**`:ro` 只影響該 socket 檔案的權限位元，不會把 Docker API 變成唯讀**——容器內仍可對 socket 送出 `POST /containers/create`、`/exec` 等寫入呼叫，進而以特權建立新容器並掛載 host 根目錄，等同 host root。alloy 同時掛載 `/proc`、`/var/log`、journal，擴大了讀取面。

**加固建議**：

- alloy 收集 Docker 日誌不需直接存取 docker.sock。優先改用 journald/檔案來源，或於 host 端跑 docker socket，透過 [docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy) 之類的 proxy 只暴露 `containers` 讀取端點（`POST=0`），再讓 alloy 連 proxy。
- 若必須直接掛載，至少加 `security_opt: ["no-new-privileges:true"]`、`read_only: true`，並把 socket 暴露面收斂到 proxy。
- 一般原則：**任何具備 docker.sock 存取的容器都應視為具 host root 權限**，需與最高敏感度服務同等對待。

### 1.3（中）dcgm-exporter 使用 `cap_add: SYS_ADMIN`

`SYS_ADMIN` 幾乎等同 root（可掛載檔案系統、操作 namespace 等）。對 GPU metrics 而言通常可用更窄的能力。

**加固建議**：先 `cap_drop: ["ALL"]` 再僅加實測必需的能力；加上 `security_opt: ["no-new-privileges:true"]`。在 profile `gpu` 未啟用時此服務不會起，但配置仍應收斂以防誤啟。

### 1.4（中）所有自建服務容器以 root 執行、無 rootfs 防護

七個 Dockerfile（layer1/2/4、dashboard、ingester、log-archiver、logbert）皆無 `USER` 指令，容器內以 uid 0 執行；compose 亦無 `read_only`、`cap_drop`、`security_opt: no-new-privileges`、`tmpfs`。任何一個服務若被 RCE（例如下方 §3 的 prompt-injection 路徑），攻擊者即在容器內握有 root，逃逸門檻降低。

**加固建議**：在每個 Dockerfile 建立非特權使用者：

```dockerfile
RUN useradd --system --uid 10001 --no-create-home aads && \
    chown -R aads:aads /app
USER 10001
```

並在 compose 對每個自建服務加上：

```yaml
    read_only: true
    cap_drop: ["ALL"]
    security_opt: ["no-new-privileges:true"]
    tmpfs: ["/tmp"]
    pids_limit: 256
    mem_limit: 1g          # 依服務調整
```

ChromaDB/模型等需寫入的路徑改用具名 volume，其餘 rootfs 保持唯讀。

### 1.5（低／中）關鍵基礎映像使用 `latest` 標籤

prometheus、grafana、cadvisor、alloy、timescaledb、litellm 皆 pin 在 `:latest`。供應鏈與可重現性風險：無法稽核實際 digest、無法保證回滾一致。

**加固建議**：pin 到具體版本並以 digest 鎖定（`image: prom/prometheus:v2.x@sha256:...`）。專案已對自建映像支援 `AADS_IMAGE_TAG`，把同樣紀律套用到基礎映像。

---

## 2. 自動化分析→修復 pipeline 的權限設計缺陷

此 pipeline 即 Layer 1 (LogBERT 異常過濾) → Layer 2 (LLM RCA、產生 FixingPlan) → Dashboard 核可 → Layer 4 executor → On-Device Agent root 執行。

### 2.1（嚴重）日誌內容 → LLM → root 執行：間接 prompt injection 鏈

被監控主機的日誌是**攻擊者可控輸入**（任何能寫進 nginx/postgres/syslog 的人都能塞入字串）。這些內容經 Layer 1 進入 Layer 2 的 LLM RCA，產出含 `runner.argv` 與 `as_root` 的 FixingPlan，最終由 Layer 4 在目標機以 root 執行。

**重要更正（與既有設計核對後）**：ExecutionProfile **並非**由 LLM 自由產生。它由 `layer2-analyzer/src/runner_catalog.py::execution_profile_for`（line 242）**確定性地**從 catalog 自己發出的 `RunnerSpec` 加上靜態 per-service 路徑表推導，原始碼明確記載「the LLM never contributes to it」（runner_catalog.py:247）。每筆授權都是 exact-command grant（`allow_extra_args=False`，line 248-251）——完整 argv 被釘死，`systemctl is-active nginx` 不會順帶授權 `systemctl stop nginx`。因此「讓被污染元件自我簽發權限白名單」這個原始命題**不成立**；不必新增「靜態能力範本」的建議（該機制已實作，見正面項）。

殘餘風險因此**收斂**為：LLM 仍能決定**呼叫哪些 catalog 內合法的 operation**。攻擊者透過 injection 無法越過白名單寫出任意 argv，但可能誘導 LLM 為了「修復」而選出一組 **catalog 合法卻過廣**的操作集合（例如非必要的重啟/設定變更），其組合對受影響服務造成破壞或可用性損害。邊界仍維持在 PolicyCard（§2.2，已 fail-closed）+ Dashboard 人工核可：

- `AUTO_REMEDIATION_ENABLED=true` 會移除人類核可這一層；不過**生產的自動執行在 schema 層已被禁止**——`action_plan.py:323-324` 的 `model_validator` 對 `environment=="prod"` 且 `auto_execute_allowed is True` 直接 `raise ValueError`，無法產生此類計畫。
- 即使人工核可，審核者面對 LLM 選定的 operation 集合仍可能草率放行；殘餘風險在「過廣但合法」的操作組合，而非任意指令。

**加固建議（殘餘風險）**：

- **輸入消毒與標註**：已由 `log_guard.py`（ADR-007）落實（見下方對 ADR-007 的確認）；持續維護其 regex 規則與 fence 覆蓋面。
- **核可介面顯著呈現 root/mutate 步驟**：把 `as_root=true`、`side_effect=mutate` 的步驟在 UI 紅標並要求逐項確認，協助審核者辨識「過廣」的操作集合。
- **operation 集合的最小化檢視**：在核可面顯示本計畫所選 operation 數量與服務影響面，使審核者更易發現非必要的破壞性步驟。

**ADR-007（log-injection 防禦）已落實，需明確認可**：`layer2-analyzer/src/log_guard.py` 是確定性 regex 掃描器 + `fence()` 資料圍欄 + 以 `contextvars` 維護的 taint registry。所有 ingest 外部日誌的 agent 工具皆被包覆，來源標籤為 `'loki'`、`'prometheus'`、`'diagnostic_command'`（tools.py:239、95、149、248）。一旦偵測到 injection-shaped 內容，`main.py:287-298` 在 `_to_fixing_plan` 之後覆寫計畫的 `environment_policy`，同時設定三鍵 `auto_execute_allowed=False`、`requires_approval=True`、`security_review_required=True`——**被污染的計畫永遠無法自動執行**，強制走人工 Gate。日誌作為對 LLM 的不可信輸入，此鏈是目前已部署的主要緩解。

### 2.2（嚴重）`AADS_POLICY_MODE=audit` 單點關閉執行期邊界

`pi-agent/src/safety_cards/policy_card.py:27`：`POLICY_MODE = os.getenv("AADS_POLICY_MODE", "enforce")` 預設 `enforce`（違規 `deny`，good），但設為 `audit` 時所有違規降級為 `warn`，`run_command` 仍照常執行。換言之，**一個環境變數即可讓 agent 對任意 argv（含 `as_root`）放行**。`install.sh` 的註解也明確說明此設計「僅供測試 lab，生產須先加 safety-card deny 規則」。注意 `AADS_POLICY_MODE` **未**出現在 `.env.example` 或任何 compose 檔——它只靠程式碼預設值 `enforce` 把關，沒有部署層的明確設定，誤設 `audit` 不會被組態審查攔下。

此外，當 `execution_profile` 為 `None`（legacy 3.0 計畫）時，enforce 模式回 `profile_missing` → deny（正確），但 audit 模式則是 warn → 執行。AuditHook 永遠 allow，PolicyCard 是唯一能 deny 的關卡。

**加固建議**：

- 生產映像/部署中**移除 audit 逃生口**，或讓 agent 在 `ENVIRONMENT != "test"` 時忽略 `AADS_POLICY_MODE=audit` 並強制 enforce。
- 在 `/v1/node/facts` 已回報 `policy_mode`；讓 Layer 4 在派送前**核對節點處於 enforce**，否則拒派並告警。
- `profile_missing` 一律 deny（不分模式）。把「無 profile 即拒絕」寫成不可被環境變數覆蓋的硬規則。

### 2.3（嚴重）`as_root` 路由到全權 root runner，無 argv 約束

`pi-agent/wrappers/aads-root-command-runner` 從 stdin 讀 JSON `{argv,cwd,env,timeout}` 並以 root `subprocess.run(shell=False)` 執行**任意 argv**，不做任何指令約束。sudoers 對 `aads-agent` 授予此 wrapper 的 `NOPASSWD`。`shell=False` 擋掉了 shell 注入，但擋不住「argv 本身就是危險指令」。整個 root 權限的控制完全外移到 §2.2 的 PolicyCard。

**加固建議**：

- 在 root runner **內**再加一層獨立於應用層的硬性 deny-list / allow-list（argv0 與前綴），作為縱深防禦——即使 PolicyCard 被旁路，OS 端仍有第二道關。
- root runner 應**清空繼承環境**而非 `os.environ.copy()`（見 §2.4）。
- 保留 V1 的精神：對高風險操作回到「具體 wrapper + 精確 sudoers grant」，把全權 runner 限制在明確標示的維運場景。

### 2.4（中）root runner 接受呼叫端 `env` 注入

root runner 以 `os.environ.copy()` 為基底，再覆寫呼叫端傳入的 `env`。雖然 PolicyCard 在 enforce 下對非空 `env` 直接回 `env_not_allowed`（good），但這仍是「應用層擋、OS 層不擋」的依賴。可控環境變數（如 `LD_PRELOAD`、`PATH`、`PYTHONPATH`）對 root 程序是經典提權途徑。

**加固建議**：root runner 自身應以最小白名單方式建構環境（只保留 `PATH=/usr/sbin:/usr/bin:/sbin:/bin` 等必需項），明確剝除 `LD_*`、`PYTHON*` 等危險變數，不信任輸入 `env`。

### 2.5（高）ingester 異常注入端點無驗證

`ingester`（埠 8000）對外提供 anomaly 寫入，Dockerfile/compose 未見任何認證。攻擊者若可達該埠即可注入偽造異常，驅動整條 pipeline（放大 §2.1）。同時該埠預設綁 `0.0.0.0`（見 §4.1）。

**加固建議**：為 ingester 加上 bearer token 或 mTLS；移除對外埠綁定（僅留在 `observability` 內網）；對寫入做速率限制與 schema 驗證。

### 2.6（高）`PI_AGENT_TOKEN` 為跨節點共用、且以 HTTP 明文傳輸

README 指出 `PI_AGENT_TOKEN` 是「shared server-to-agent bearer token」，且驗證範例為 `http://<target>:8090`（明文）。`layer4-executor` 的 `call_agent` 用 `aiohttp` 直接打 `base_url`，無 TLS、無憑證驗證。後果：(a) 單一 token 外洩即危及**所有**節點；(b) token 在網路上可被竊聽；(c) agent 端 `require_auth` 只做字串相等比較（非 constant-time，理論上有 timing 面）。

**加固建議**：

- 改為**每節點獨立 token**（註冊時由 server 簽發、可個別撤銷）。
- agent 端強制 **mTLS / HTTPS**，executor 端開啟憑證驗證（`aiohttp` 預設驗證，但需確保 `base_url` 為 https 且不停用驗證）。
- token 比較改用 `hmac.compare_digest`。

### 2.7（中）agent `base_url` 由自我註冊、executor 將 token 送往該 URL

`dashboard` 的 `register_agent` 把 `base_url` 原樣寫入 `agent_nodes`，`layer4-executor` 之後把 `PI_AGENT_TOKEN` 當 Bearer 送到該 `base_url`。註冊需 admin key，但 admin key 預設為 `change-me-admin-key`（§4.3）。若註冊被濫用，可把 `base_url` 指向攻擊者主機，**誘使 executor 外送 PI_AGENT_TOKEN**（token 竊取 / SSRF）。

**加固建議**：對 `base_url` 做嚴格白名單（限定內網網段／已知主機名稱）；註冊與後續變更分離權限；token 改為 server 主動下發而非由 executor 送往任意註冊位址。

---

## 3. pi-agent systemd unit 的 ReadWritePaths 與沙箱配置

`pi-agent/systemd/aads-agent.service` 已具備部分 hardening（`ProtectSystem=strict`、`PrivateTmp=true`、`ReadOnlyPaths` 鎖住資料目錄），方向正確，但有幾處削弱與缺漏。

### 3.1（高）`NoNewPrivileges=false` 抵銷大部分 namespace 防護

unit 明確設 `NoNewPrivileges=false`——這是為了讓 agent 能 `sudo` 到 root runner 而必須如此。但其代價是：service 程序可透過 setuid（sudo）取得權限，且 sudo 生出的 root 子程序雖仍在同一 mount namespace（`ProtectSystem`、`ReadWritePaths` 仍部分適用），卻以 **真實 root** 身分執行，能力不受 unit 限制（unit 未設 `CapabilityBoundingSet`）。結果是 `ProtectSystem=strict` 等防護對「經由 sudo 的 root 路徑」幾乎是裝飾性的。

**加固建議**：此為架構取捨，無法單靠 unit 解決，但可：在 root runner 端補硬性約束（§2.3）；對 service 本體（非 sudo 路徑）仍應盡量收緊（下列各項）；長期考慮以 polkit/具體 wrapper 取代「NoNewPrivileges=false + 全權 runner」。

### 3.2（中）`ReadWritePaths` 含 `/run`，範圍過寬

`ReadWritePaths=... /var/lib/aads-agent ... /run /tmp`。`/run` 是全系統 runtime 狀態（pid 檔、unix socket、`/run/systemd` 等）。對 `/run` 的可寫存取可被用來干擾其他服務（覆寫 pid 檔、放置/竄改 socket）。`/tmp` 因 `PrivateTmp=true` 已隔離，風險較低。

**加固建議**：移除整個 `/run`，改為精確列出真正需要的子路徑（例如某服務的 `/run/<svc>.pid` 或重載所需的特定 socket 目錄）。各服務 `-/etc/...`、`-/var/log/...` 的「`-` 可選前綴」用法正確（路徑不存在不報錯），保留即可。

### 3.3（中）缺少 kernel/capabilities/syscall 層防護

unit 未設定下列常見加固指令，使（非 sudo 路徑的）service 本體仍可觸及不必要的 kernel 介面：

`CapabilityBoundingSet`、`AmbientCapabilities=`（清空）、`SystemCallFilter=@system-service` + `SystemCallArchitectures=native`、`ProtectKernelModules=true`、`ProtectKernelTunables=true`、`ProtectKernelLogs=true`、`ProtectControlGroups=true`、`ProtectClock=true`、`ProtectHostname=true`、`ProtectProc=invisible`、`ProcSubset=pid`、`RestrictNamespaces=true`、`RestrictRealtime=true`、`RestrictSUIDSGID=true`、`RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`、`LockPersonality=true`、`MemoryDenyWriteExecute=true`、`SystemCallErrorNumber=EPERM`、`UMask=0077`、`IPAddressDeny=any` + `IPAddressAllow=`（限定 server）。

**加固建議**：加入上述指令。注意：因 §3.1 需保留 sudo，`SystemCallFilter`/`CapabilityBoundingSet` 不能擋住 sudo 路徑（sudo 本身需要相關 syscall），但仍能限制 agent 本體被 RCE 後的橫向能力，屬有效縱深防禦。建議用 `systemd-analyze security aads-agent` 量測強化前後分數。

### 3.4（低）`agent.env` 權限與 token 落地

`install.sh` 將 `agent.env`（含 `AADS_AGENT_TOKEN`）設為 `0640 root:aads-agent`——讓 service 帳號可讀，合理。但 token 以明文長存於檔案；配合 §2.6 的共用/明文傳輸問題，外洩面偏大。

**加固建議**：搭配 §2.6 改每節點 token、加 TLS；視需要用 `systemd` 的 `LoadCredentialEncrypted=` 取代明文 EnvironmentFile。

---

## 4. Docker Compose 常見錯誤配置

### 4.1（高）服務埠預設綁定 `0.0.0.0`，內部服務對外暴露

`ports:` 全部寫成 `"5432:5432"`、`"3100:3100"`、`"4000:4000"`、`"8080:8080"`、`"8000:8000"`、`"9090:9090"` 等——Docker 預設綁 host 全部介面。**TimescaleDB（5432）、Loki（3100）、LiteLLM（4000）、ingester（8000）、Layer 2（8080）、Prometheus（9090）等本應僅供內網的服務，被暴露在主機所有 IP 上**。資料庫直接對外是高風險（配合 §4.3 預設密碼）。

**加固建議**：對所有非「需對外」的服務改綁 loopback，或完全移除 `ports` 只留 `observability` 內網互通：

```yaml
    ports:
      - "127.0.0.1:5432:5432"   # DB 僅本機
```

真正需要對外的只有 Dashboard（5000）與（視情況）Grafana（3000）。其餘移除主機埠映射，服務間透過 compose network 名稱互連即可。

### 4.2（高）Grafana 匿名存取 + 預設帳密

`GF_AUTH_ANONYMOUS_ENABLED=true`（Viewer），且 dev compose 寫死 `GF_SECURITY_ADMIN_USER=admin / GF_SECURITY_ADMIN_PASSWORD=admin`；prod 雖改為 `${GF_SECURITY_ADMIN_PASSWORD:-admin}` 但預設仍是 `admin`，且 `.env.example` 也填 `admin`。匿名可瀏覽所有儀表板（含潛在敏感的系統/日誌資訊）。

**加固建議**：生產關閉 `GF_AUTH_ANONYMOUS_ENABLED`；強制由環境變數提供強密碼且**無預設值**（缺值即啟動失敗）；考慮接 SSO。

### 4.3（高）預設密鑰遍布，且以環境變數注入

`.env.example` 的 `TIMESCALEDB_PASSWORD=logdb_password_change_me`、`AADS_ADMIN_API_KEY=change-me-admin-key`、`LITELLM_MASTER_KEY=sk-aads-dev`、`PI_AGENT_TOKEN=change-me-agent-token` 皆有可運作的弱預設值。任何忘記覆寫的部署即帶著公開已知密鑰上線。Admin key 預設值同時放大 §2.7。密鑰經 `environment:` 注入，可被 `docker inspect` 或 `/proc/<pid>/environ` 讀出。

**加固建議**：

- 移除所有可運作的預設值，缺值即啟動失敗（`${VAR:?must be set}`）。
- 密鑰改用 Docker secrets / 檔案掛載（`*_FILE` 慣例）而非 `environment`，避免出現在 inspect/environ。
- 提供 `install-server.sh` 已具備的密鑰自動產生流程作為唯一正路，文件移除明文範例值或標註「僅佔位、嚴禁沿用」。

### 4.4（中）Prometheus 開放 lifecycle 與 remote-write 接收

`--web.enable-lifecycle`（允許 `POST /-/reload`、甚至 `/-/quit`）與 `--web.enable-remote-write-receiver`（允許任意寫入 metrics），配合 §4.1 的 9090 對外暴露，等於開放未認證的設定重載與資料注入。

**加固建議**：移除 9090 的對外埠映射；若不需要遠端重載則拿掉 `--web.enable-lifecycle`；remote-write 接收端置於內網並加反向代理認證。

### 4.5（中）cadvisor / alloy 的 host 掛載面（與 §1 交叉）

`cadvisor` 掛載 `/`、`/var/run`、`/sys`、`/var/lib/docker`；`alloy` 掛載 docker.sock、`/proc`、`/var/log`。即使多為 `:ro`，仍構成大面積 host 可視性，且 docker.sock 的 `:ro` 無實質防護（§1.2）。

**加固建議**：見 §1.1、§1.2。最小化掛載清單，能用 proxy/檔案來源就不要直掛 socket 與根目錄。

### 4.6（低）無資源限制，pipeline 可被放大為 DoS

所有服務皆無 `mem_limit`、`cpus`、`pids_limit`。LogBERT/Torch 與 LLM 呼叫屬重負載，攻擊者透過 §2.5 注入大量異常可放大資源消耗拖垮主機。

**加固建議**：對每個服務設定資源上限（見 §1.4 範例），對 pipeline 入口（ingester、layer1 poll）加速率限制。

### 4.7（低）本機 lab 密鑰檔存在於工作目錄

`.aads-lab-admin-key`、`.aads-lab-token`、`.env.lab` 含真實值且位於 repo 工作目錄。已確認三者皆在 `.gitignore` 中、未被 git 追蹤（good），故非版本控制外洩，但仍以明文存在本機，備份/同步時需留意。

**加固建議**：維持 gitignore；確保任何資料夾同步（如 Obsidian/雲端）排除這些檔；定期輪替 lab 密鑰。

### 4.8（高）Dashboard 以 Flask `debug=True` 綁 `0.0.0.0:5000` 啟動

`dashboard/app.py:999` 的 `__main__` 進入點是 `app.run(host='0.0.0.0', port=5000, debug=True)`。`debug=True` 會啟用 Werkzeug 互動式除錯器——任何能觸發未捕捉例外的請求即可在瀏覽器取得 Python 互動主控台（含 PIN 機制，但在容器內常可繞過或被弱化），等於應用層 RCE；同時自動重載與詳盡 traceback 也外洩內部資訊。配合 §4.1 的對外埠暴露，這是直接面向 Dashboard 使用者的高風險入口。

**加固建議**：生產**不得**以此 `__main__` 進入點啟動；改用 WSGI 伺服器（gunicorn/uwsgi）且 `debug=False`。若保留腳本入口，將 `debug` 改由環境變數控制並預設 `False`，host 綁 loopback 或交由反向代理。

### 4.9（中）Dashboard 401 回應外洩 admin key 指紋

`dashboard/app.py:70-76` 的 `key_diagnostics` 對缺失/錯誤的 admin key 回報 `present`、`length`（金鑰長度）與 `fingerprint`（金鑰的 `sha256` 前 10 字元），並用於 401 回應。對未授權呼叫者揭露所設定金鑰的長度與穩定指紋，提供了離線比對與長度推測的旁路資訊，弱化 `AADS_ADMIN_API_KEY` 的保密性。

**加固建議**：401 回應只回通用訊息（不含 length/fingerprint）；診斷資訊僅寫入伺服器端日誌（且避免記錄可逆推的指紋），不回給客戶端。

---

## 設計上的正面項（建議保留）

- **ExecutionProfile 確定性產生（即「靜態能力範本」已落實）**：`runner_catalog.execution_profile_for`（runner_catalog.py:242）只從 catalog 自己發出的 `RunnerSpec` 加靜態 per-service 路徑表推導，LLM 不參與（line 247）；每筆為 exact-command grant（`allow_extra_args=False`，line 248-251），完整 argv 釘死。這正是其他審計常建議「以靜態範本取代 LLM 自簽白名單」的做法，本系統已實作——無須再列為待辦。
- **log-injection 防禦已部署（ADR-007）**：`log_guard.py` = 確定性 regex 掃描 + `fence()` 圍欄 + `contextvars` taint registry，包覆 `loki`/`prometheus`/`diagnostic_command` 三類工具輸出（tools.py:95/149/239/248）；偵測到 injection 即在 `main.py:287-298` 將計畫設為 `auto_execute_allowed=False`、`requires_approval=True`、`security_review_required=True`，被污染計畫永遠無法自動執行。
- **生產自動執行於 schema 層被禁止**：`action_plan.py:323-324` 對 `environment=="prod"` 且 `auto_execute_allowed is True` 直接拒絕，無法產生可在生產自動執行的計畫。
- 核可流程以 `plan_sha256`（含 `execution_profile`）綁定人類所見的確切計畫內容，executor（executor.py:738）與 dashboard（app.py:30）的 hash 計算須維持 byte-identical；hash drift 會 pause 並記 `drift_type='approved_plan_hash_mismatch'`（TOCTOU 防護，ADR-006）——良好的完整性設計。
- `shell=False` 一致用於 agent 與 root runner，杜絕 shell 注入。
- PolicyCard（`pi-agent/src/safety_cards/policy_card.py`）**已 SHIP 且預設 fail-closed**：`POLICY_MODE` 預設 `enforce`（line 27），對路徑參數檢查 `..`、對 `env` 非空即拒、對 `cwd` 約束、對缺 profile 回 `profile_missing`→deny。這不是「待補/未來」機制，而是出廠即生效的執行期邊界。
- `policy_card.py`（line 60）與 layer2 `schemas/action_plan.py`（line 122）的兩份 `profile_allows` 以 `layer2-analyzer/tests/test_parity.py` 釘住**判定結果一致**（`l2_result == pi_result`，非 byte-identical 原始碼），避免雙份實作的 verdict 漂移。
- systemd unit 已用 `ProtectSystem=strict` + `ReadOnlyPaths` 鎖住資料目錄，基礎方向正確。

維持這些設計，並把上面的單點失效（audit 逃生口、全權 runner、docker.sock、預設密鑰、對外埠）逐一補強，可顯著抬高整體攻擊門檻。

---

## 優先順序建議（加固路線）

**第一波（堵住單點瓦解整條邊界者）**：§2.2 移除 audit 逃生口並強制 enforce、§4.3 移除所有預設密鑰、§4.1 收斂對外埠（尤其 5432）、§1.1+§1.2 處理 privileged cadvisor 與 docker.sock。

**第二波（縱深防禦）**：§4.8 Dashboard 關閉 `debug=True` 並改用 WSGI 伺服器、§2.3+§2.4 root runner 加硬性約束與環境清洗、§2.6 每節點 token + TLS、§2.5 ingester 認證、§4.2 Grafana 關匿名、§4.9 移除 401 金鑰指紋外洩。

**第三波（收斂與量測）**：§1.4 容器非 root + read-only、§3.2+§3.3 systemd 沙箱補強並用 `systemd-analyze security` 量測、§1.5 映像 pin digest、§4.6 資源限制。
