# Obsidian Vault 同步指南

本文件說明如何把 AADS 的 Obsidian 文檔 vault 同步到 MinIO（S3 相容）物件儲存，
支援兩種方式：

- **(A) GUI 同步** — Obsidian 內建的 **Remotely Save** 社群插件（多設備、自動同步）。
- **(B) CLI 同步** — repo 內的 `scripts/sync-obsidian` 腳本（無 GUI 環境、CI、批次備份）。

兩者寫入同一個 bucket，可混用。

## MinIO 連線資訊（權威值）

| 項目 | 值 |
| --- | --- |
| Endpoint | `https://s3.tfbs.site` |
| Bucket | `obsidian-aads` |
| Console | `https://console.tfbs.site` |
| Region | `us-east-1` |

## vault 在哪裡：in-repo vs 外部同步 vault

兩個位置容易混淆，務必分清楚：

- **In-repo `docs/obsidian-vault/`** — 在 `.gitignore`（第 59 行 `docs/obsidian-vault/`）內，
  **不被 git 追蹤**。目前磁碟上只有 `ADR/` 子目錄（3 個檔案：ADR-005 / ADR-006 / ADR-007）。
  `Technical-Reports/`、`Research/`、`Infrastructure/`、`Services/`、`Database/` 等子目錄**並不存在**。
  `Architecture-Overview.canvas` 位於 **repo 根目錄**（`./Architecture-Overview.canvas`），不在 vault 內。
- **外部同步 vault `/Volumes/eSSD/obsidian-aads`** — `scripts/sync-obsidian` 的預設來源
  （`AADS_OBSIDIAN_VAULT` 預設值）。這是日常用 Obsidian 開啟、編輯、跨設備同步的完整 vault，
  與 MinIO bucket 對應。CLI 腳本鏡像的是**這個外部 vault**，不是 in-repo 的 `docs/obsidian-vault/`
  （除非你把 `AADS_OBSIDIAN_VAULT` 指向後者）。

> 為什麼不用 git 同步 vault？多設備同時編輯 Markdown 容易產生 merge 衝突，
> 改用物件儲存（Remotely Save / CLI sync）作為單一同步來源。已 commit 過的舊文檔仍保留在
> git 歷史，可用 `git show <commit>:docs/obsidian-vault/...` 查看（見 commit `7f4611c`）。

---

## (A) GUI 同步：Remotely Save 插件

適合在桌面 Obsidian 上自動、雙向同步多台設備。

### 1. 安裝插件

Obsidian 中：Settings → Community plugins → Browse → 搜尋 **"Remotely Save"** → Install → Enable。

### 2. 設定 MinIO（S3）後端

Settings → Remotely Save：

```
Service:    S3
Endpoint:   https://s3.tfbs.site
Bucket:     obsidian-aads
Region:     us-east-1
Access Key: <你的 MinIO access key>
Secret Key: <你的 MinIO secret key>
```

### 3. 同步行為設定（建議）

```
Auto Sync:    ON（例如每 5 分鐘）
Sync on Save: ON（儲存後數秒）

忽略路徑（每台設備各自管理，不要同步）:
- .obsidian/workspace*
- .obsidian/cache
- .trash/
```

### 4. 手動觸發同步

- 命令面板（Ctrl/Cmd+P）→ `Remotely Save: Sync`
- 或點擊側邊欄的雲端圖示

### 5. 處理衝突

多設備同時編輯可能產生 `.conflict` 檔案：開啟衝突檔與原檔，手動合併內容，刪除 `.conflict`
檔，再同步一次。

### 6. 在新設備上設定

安裝 Obsidian → 建立/開啟 vault → 安裝 Remotely Save → 填入與上方**完全相同**的 MinIO 設定
→ 執行同步即會下載既有內容。

### 安全性

- 傳輸經 HTTPS (TLS)。
- 憑證存在本地 `<vault>/.obsidian/plugins/remotely-save/data.json`，**不應同步、不應進 git**
  （注意 CLI 腳本也會排除 `data.json`，見下節）。
- 建議定期輪換 MinIO access key。

---

## (B) CLI 同步：`scripts/sync-obsidian`

repo 內的真實工具是 **bash 腳本** `scripts/sync-obsidian`（`#!/usr/bin/env bash`），
用 `mc mirror` 或 `aws s3 sync` 把整個 vault 鏡像到 MinIO。適合無 GUI 環境、伺服器、批次備份。

> 此腳本**不是** Python，不使用 boto3，**不會**讀取 `data.json` 取得設定，也**不會**自動建立
>「上傳 N 個檔案」的統計摘要。所有設定都來自環境變數（見下）。

### 用法

```bash
# 從 repo 根目錄執行
bash scripts/sync-obsidian            # 實際同步
bash scripts/sync-obsidian --dry-run  # 預演，不實際寫入
```

### 環境變數與預設值

| 變數 | 預設值 | 說明 |
| --- | --- | --- |
| `AADS_OBSIDIAN_VAULT` | `/Volumes/eSSD/obsidian-aads` | 本地 vault 來源目錄（不存在會直接報錯退出） |
| `AADS_OBSIDIAN_BUCKET` | `obsidian-aads` | 目標 bucket |
| `AADS_OBSIDIAN_ENDPOINT` | `https://s3.tfbs.site` | S3 endpoint（僅 `aws` 路徑使用） |
| `AADS_OBSIDIAN_MC_TARGET` | （空） | `mc` 別名目標（如 `myminio`）；設定後才會走 `mc` 路徑 |

### 後端選擇順序（mc 先於 aws）

腳本依序判斷，用第一個可用的後端：

1. **`mc`（MinIO Client）** — 條件：系統有 `mc` **且** `AADS_OBSIDIAN_MC_TARGET` 非空。
   執行 `mc mirror --overwrite <vault> <MC_TARGET>/<bucket>`（`--dry-run` 時加上 `--dry-run`）。
2. **`aws` CLI** — 上述條件不成立時的後援。
   執行 `aws s3 sync <vault> s3://<bucket> --endpoint-url <endpoint>`（`--dry-run` 對應 `--dryrun`）。
3. 兩者皆無 → 印出錯誤並以非 0 退出，提示安裝其一或設定 `AADS_OBSIDIAN_MC_TARGET`。

### 排除清單（兩種後端共用）

腳本以下列 glob 排除（**沒有副檔名 allowlist**，預設鏡像 vault 內其餘所有檔案）：

```
.obsidian/**
.trash/**
**/.DS_Store
**/workspace.json
**/workspace-mobile.json
**/data.json
```

> 注意：排除的是 `.obsidian/`、`.trash/`、`.DS_Store`、`workspace*.json`、`data.json`——
> 即每台設備本地的設定與快取，避免覆蓋彼此。其餘 `.md` / `.canvas` / 圖片等都會被鏡像。

### 憑證前置需求

腳本本身**不處理憑證**，請先設定好所選後端的存取憑證：

- **走 `mc`**：先建立 alias，例如
  ```bash
  mc alias set myminio https://s3.tfbs.site <ACCESS_KEY> <SECRET_KEY>
  export AADS_OBSIDIAN_MC_TARGET=myminio
  ```
- **走 `aws`**：用標準 AWS 憑證來源（`aws configure`、`~/.aws/credentials`、
  或 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 環境變數）。

### 典型流程

```bash
# 1. 編輯 vault 內文檔（外部 vault 或 in-repo，視 AADS_OBSIDIAN_VAULT 而定）
vim /Volumes/eSSD/obsidian-aads/ADR/ADR-008-Something.md

# 2. 先預演確認要上傳哪些變更
bash scripts/sync-obsidian --dry-run

# 3. 實際同步
bash scripts/sync-obsidian
```

### 對 in-repo `docs/obsidian-vault/` 同步

若要鏡像 repo 內的 ADR 子集而非外部 vault，把來源指向 repo 路徑：

```bash
AADS_OBSIDIAN_VAULT="$(git rev-parse --show-toplevel)/docs/obsidian-vault" \
  bash scripts/sync-obsidian --dry-run
```

---

## 故障排除

### 連線失敗

```bash
curl -I https://s3.tfbs.site   # 確認 endpoint 可達
```

### bucket 不存在 / 無權限

前往 `https://console.tfbs.site` 確認 `obsidian-aads` bucket 存在且帳號有寫入權限。

### CLI 腳本報「vault not found」

```
Obsidian vault not found: /Volumes/eSSD/obsidian-aads
```

外接磁碟未掛載或路徑不對 → 掛載磁碟，或用 `AADS_OBSIDIAN_VAULT` 指向正確目錄。

### CLI 腳本報「Neither configured mc nor aws CLI is available」

未安裝 `mc`/`aws`，或想用 `mc` 卻沒設 `AADS_OBSIDIAN_MC_TARGET` 而 `aws` 也不存在 →
安裝其一並設好憑證（見上方前置需求）。

---

## 參考

- [Remotely Save GitHub](https://github.com/remotely-save/remotely-save)
- 真實 CLI 腳本：`scripts/sync-obsidian`
- ADR 文檔：`docs/obsidian-vault/ADR/`（ADR-005 / 006 / 007）

---

**狀態**: 可用
