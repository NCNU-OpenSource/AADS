# AI Auto Debug System — Codex / Agent rules

> The canonical engineering guide for this repo is [`.claude/CLAUDE.md`](.claude/CLAUDE.md)
> — architecture, the numbered layers, the execution-path security model, test
> commands, and conventions. **Read it first.** This file only adds the
> Obsidian-documentation workflow and a few agent-specific reminders.

## Obsidian 文檔同步

同步工具是 **`bash scripts/sync-obsidian`**（一支 bash 腳本，用 `mc mirror` 或
`aws s3 sync` 把 Obsidian vault 鏡像到 MinIO）。**沒有 Python skill** —
`~/.claude/skills/` 與 `~/.Codex/skills/` 都不存在這支工具。

### 執行方式

```bash
bash scripts/sync-obsidian --dry-run   # 先預覽要同步的檔案
bash scripts/sync-obsidian             # 實際鏡像到 MinIO
```

由環境變數設定：

| 變數 | 用途 | 預設 |
| --- | --- | --- |
| `AADS_OBSIDIAN_VAULT` | 要同步的 vault 路徑（外接 SSD） | `/Volumes/eSSD/obsidian-aads` |
| `AADS_OBSIDIAN_BUCKET` | MinIO bucket | `obsidian-aads` |
| `AADS_OBSIDIAN_ENDPOINT` | MinIO endpoint | `https://s3.tfbs.site` |
| `AADS_OBSIDIAN_MC_TARGET` | 選用的 `mc` alias | — |

腳本鏡像 **整個 vault**，排除 `.obsidian/`、`.trash/`、`.DS_Store`、`workspace*.json`、
`data.json`（**沒有副檔名白名單**）。完整說明見 [docs/OBSIDIAN_SYNC.md](docs/OBSIDIAN_SYNC.md)。

### MinIO 連線資訊

- **Endpoint**: https://s3.tfbs.site
- **Bucket**: obsidian-aads
- **Console**: https://console.tfbs.site

## 專案特定規則

### 文檔結構（現況）

repo 內的 `docs/obsidian-vault/` **目前只有**：

```
docs/obsidian-vault/
└── ADR/                    # Architecture Decision Records (ADR-005 / 006 / 007)
```

`Technical-Reports/`、`Research/`、`Infrastructure/`、`Services/`、`Database/`
**尚未建立**；`Architecture-Overview.canvas` 位於 **repo 根目錄**（不在 vault 內）。
注意 `scripts/sync-obsidian` 同步的是 `AADS_OBSIDIAN_VAULT` 指向的外接 SSD vault，
與 repo 內的 `docs/obsidian-vault/` 是兩份不同的東西。

### 文檔更新原則

1. 重大技術選型／架構／API 決策必須寫 ADR（放 `docs/obsidian-vault/ADR/`）。
2. 架構變更同步更新根目錄的 `Architecture-Overview.canvas`。
3. 實作或 bug 修復完成後更新對應的 `docs/` 文檔。

### 執行路徑安全（必讀）

動到 Layer 4 執行路徑前，先讀 [`docs/SECURITY_HARDENING_AUDIT.md`](docs/SECURITY_HARDENING_AUDIT.md)
與 ADR-005/006/007。關鍵不變量：

- `FixingPlan 3.1` 的必填 `execution_profile`（由 `runner_catalog` 決定性產生，非 LLM）。
- PolicyCard fail-closed（`AADS_POLICY_MODE=enforce` 為預設）。
- `plan_sha256` 漂移／TOCTOU 防護；失敗走 `paused_for_review` + `execution_escalations`。
- `log_guard` 日誌注入防禦（外部日誌是不可信輸入）。
- `profile_allows()` 在 `layer2-analyzer/src/schemas/action_plan.py` 與
  `pi-agent/src/safety_cards/policy_card.py` 各有一份 —— 改一邊就要改另一邊，
  否則 `layer2-analyzer/tests/test_parity.py` 會擋下。
