# Obsidian CLI 同步指南

## 概述

在無 GUI 環境下手動同步 Obsidian vault 到 MinIO S3 儲存。

## 安裝

Skill 已安裝在: `~/.claude/skills/sync-obsidian`

## 使用方式

### 方法 1: Shell Alias（推薦）

```bash
# 重新載入 shell 配置
source ~/.zshrc  # 或 source ~/.bashrc

# 同步
sync-obsidian
```

### 方法 2: 直接執行

```bash
python3 ~/.claude/skills/sync-obsidian
```

### 方法 3: Claude Code Skill

在 Claude Code 中執行:
```
/sync-obsidian
```

## 工作流程

### 典型使用場景

```bash
# 1. 修改文檔
vim docs/obsidian-vault/ADR/ADR-004-New-Feature.md

# 2. 同步到 MinIO
sync-obsidian

# 3. 在其他設備（GUI）上
# Obsidian 會自動下載更新
```

### 批次修改後同步

```bash
# 添加多個新文檔
echo "# New Research" > docs/obsidian-vault/Research/New-Topic.md
echo "# New ADR" > docs/obsidian-vault/ADR/ADR-004-Decision.md

# 一次同步
sync-obsidian
```

## 輸出說明

```
✓ Connected to: https://s3.tfbs.site       # 連線成功
✓ Bucket: obsidian-aads                    # Bucket 確認

📁 Syncing: docs/obsidian-vault            # 開始掃描

✓ ADR/ADR-001-Metrics-Collection.md       # 上傳進度
✓ Services/FastAPI Ingester.md
...

============================================================
📊 Sync Summary
============================================================
✓ Uploaded:  40 files                      # 成功上傳文件數
⊘ Skipped:   6 files                       # 跳過文件（.obsidian）
📦 Size:     178,205 bytes (174.0 KB)      # 總大小
❌ Errors:   0                              # 錯誤數
============================================================

✅ Sync complete!
🔗 View at: https://console.tfbs.site      # Web Console 連結
```

## 配置

Skill 自動讀取配置: `docs/obsidian-vault/.obsidian/plugins/remotely-save/data.json`

當前設定:
- **Endpoint**: https://s3.tfbs.site
- **Bucket**: obsidian-aads
- **Region**: us-east-1

## 同步規則

### 上傳的文件
- ✅ `.md` 文件（Markdown）
- ✅ `.canvas` 文件（Canvas 圖表）

### 跳過的文件
- ❌ `.obsidian/` 目錄（每台設備各自管理）
- ❌ `.trash/` 目錄
- ❌ 其他非文檔文件

## 故障排除

### 連線失敗

```bash
❌ Connection failed: [Errno -2] Name or service not known
```

**解決方案**:
1. 檢查網路連線
2. 確認 MinIO endpoint 可訪問: `curl -I https://s3.tfbs.site`

### Bucket 不存在

```bash
❌ Cannot access bucket 'obsidian-aads': An error occurred (404) when calling the HeadBucket operation
```

**解決方案**:
1. 前往 https://console.tfbs.site
2. 創建 bucket: `obsidian-aads`

### 配置文件遺失

```bash
❌ Failed to read config: [Errno 2] No such file or directory
```

**解決方案**:
```bash
# 檢查配置文件是否存在
ls -la docs/obsidian-vault/.obsidian/plugins/remotely-save/data.json

# 如果不存在，重新配置 Remotely Save
# 參考: docs/OBSIDIAN_SYNC_SETUP.md
```

## Git 整合

### Obsidian vault 不追蹤

`docs/obsidian-vault/` 已加入 `.gitignore`，不會被 git 追蹤。

**查看歷史文檔**:
```bash
# 查看特定 commit 的文檔
git show 7f4611c:docs/obsidian-vault/ADR/ADR-001-Metrics-Collection.md

# 查看文檔的完整歷史
git log --all --full-history -- docs/obsidian-vault/
```

### Skill 本身的版本控制

Skill 位於 `~/.claude/skills/sync-obsidian`（用戶目錄），不在 git 版本控制中。

如果需要備份或分享:
```bash
# 備份
cp ~/.claude/skills/sync-obsidian ~/backup/

# 分享給其他用戶
scp ~/.claude/skills/sync-obsidian user@host:~/.claude/skills/
```

## 自動化

### Cron 定時同步

```bash
# 每小時同步一次
crontab -e

# 添加:
0 * * * * cd ~/Developer/ai-auto-debug-system && python3 ~/.claude/skills/sync-obsidian >> ~/sync-obsidian.log 2>&1
```

### Git Hook

在 `.git/hooks/post-commit` 中:
```bash
#!/bin/bash
# 每次 commit 後同步 obsidian
python3 ~/.claude/skills/sync-obsidian
```

## 依賴

- **Python 3**: 必須
- **boto3**: 必須（S3 client）
- **網路連線**: 必須（訪問 MinIO）

檢查依賴:
```bash
python3 --version
python3 -c "import boto3; print('boto3:', boto3.__version__)"
```

## 相關文檔

- [OBSIDIAN_SYNC_SETUP.md](./OBSIDIAN_SYNC_SETUP.md) - 完整設定指南
- [OBSIDIAN_SETUP.md](./OBSIDIAN_SETUP.md) - Remotely Save 安裝

---

**更新**: 2026-04-08  
**狀態**: ✅ 可用
