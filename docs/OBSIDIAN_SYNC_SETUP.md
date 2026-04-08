# Obsidian Remotely Save - 配置完成

## ✅ 已完成設定

### 1. 插件安裝
- ✅ Remotely Save v0.5.25 已下載
- ✅ 插件已啟用
- ✅ 配置檔案已創建

### 2. MinIO 連線設定
```
Endpoint: https://s3.tfbs.site
Bucket: obsidian-sync
Region: us-east-1
Access Key: bs10081
Status: 已配置
```

### 3. 同步設定
- **自動同步**: 每 5 分鐘
- **儲存後同步**: 5 秒後
- **忽略路徑**:
  - `.obsidian/workspace*`
  - `.obsidian/cache`
  - `.trash/`

## 🚀 使用步驟

### 首次同步（當前主機）

1. **開啟 Obsidian**
   ```bash
   cd ~/Developer/ai-auto-debug-system/docs/obsidian-vault
   obsidian .
   ```

2. **檢查插件狀態**
   - 左側邊欄應該會看到 Remotely Save 圖示
   - Settings → Community plugins → 確認 Remotely Save 已啟用

3. **執行首次同步**
   - 點擊 Remotely Save 圖示
   - 或使用命令面板 (Ctrl+P): "Remotely Save: Sync"
   - 首次會上傳所有文件到 MinIO

4. **驗證同步**
   - 前往 https://console.tfbs.site
   - 登入後檢查 `obsidian-sync` bucket
   - 應該看到所有 .md 和 .canvas 文件

### 在其他設備同步

1. **安裝 Obsidian**

2. **創建/開啟 vault**

3. **安裝 Remotely Save**
   - Settings → Community plugins → Browse → "Remotely Save"

4. **配置相同的 MinIO 設定**
   ```
   Service: S3
   Endpoint: https://s3.tfbs.site
   Bucket: obsidian-sync
   Access Key: bs10081
   Secret Key: (你的密碼)
   Region: us-east-1
   ```

5. **執行同步**
   - 會自動下載所有文件

## 📋 從 Git 移除追蹤

目前 `docs/obsidian-vault/` 已加入 `.gitignore`，新的修改不會被追蹤。

要完全從 git 移除追蹤（保留本地文件）：

```bash
# 停止追蹤
git rm --cached -r docs/obsidian-vault/

# Commit
git add .gitignore
git commit -m "chore: migrate obsidian-vault from git to Remotely Save

- Stop tracking docs/obsidian-vault/ in git
- Switch to Remotely Save for cross-device sync
- Avoids merge conflicts when editing on multiple devices
- Previous documentation preserved in git history (commit a01996c)"

# Push
git push origin master
```

**注意**: 
- 已 commit 的文檔會保留在 git 歷史中
- 可透過 `git show a01996c:docs/obsidian-vault/...` 查看
- 本地文件不會被刪除

## 🔧 配置檔案位置

```
docs/obsidian-vault/.obsidian/plugins/remotely-save/
├── main.js              (插件主程式)
├── manifest.json        (插件資訊)
├── styles.css           (樣式)
└── data.json            (你的 MinIO 設定，已在 .gitignore)
```

## 💡 使用技巧

### 1. 編輯前同步
開啟 Obsidian → 等待自動同步完成（狀態欄顯示）→ 開始編輯

### 2. 手動同步
- **快捷鍵**: Ctrl+P → "Remotely Save: Sync"
- **側邊欄**: 點擊 cloud 圖示

### 3. 查看同步狀態
- 狀態欄會顯示最後同步時間
- 點擊查看詳細日誌

### 4. 處理衝突
如果出現 `.conflict` 文件：
1. 打開衝突文件和原文件
2. 手動合併內容
3. 刪除 `.conflict` 文件
4. 再次同步

## 🔐 安全性

- **傳輸加密**: HTTPS (TLS)
- **憑證儲存**: 本地 data.json（已在 .gitignore）
- **建議**: 定期更換 MinIO Access Key

## 📊 監控

### MinIO Console
https://console.tfbs.site
- 查看儲存用量
- 檢視文件列表
- 設定存取權限

### Remotely Save 日誌
Obsidian → Settings → Remotely Save → View logs

## 🆘 故障排除

### 同步失敗

1. **檢查網路連線**
   ```bash
   curl -I https://s3.tfbs.site
   ```

2. **驗證憑證**
   - Settings → Remotely Save → 重新輸入密碼

3. **檢查 bucket 權限**
   - 在 MinIO console 確認 bucket 存在且有寫入權限

### 重置同步

如果需要完全重置：
1. 在 MinIO console 清空 `obsidian-sync` bucket
2. Obsidian → Remotely Save → Settings → "Reset local records"
3. 重新執行同步

---

**設定日期**: 2026-04-08
**插件版本**: Remotely Save 0.5.25
**狀態**: ✅ 已配置完成，可以開始使用
