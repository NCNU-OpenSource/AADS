# Obsidian Remotely Save 設置指南

## 為什麼使用 Remotely Save？

- ✅ 避免多設備編輯時的 git 衝突
- ✅ 自動同步，無需手動 commit
- ✅ 支援多種儲存後端（S3, WebDAV, OneDrive, Dropbox）
- ✅ 版本歷史和衝突解決機制

## 快速開始

### 1. 安裝插件

在 Obsidian 中：
- Settings → Community plugins → Browse
- 搜尋 "Remotely Save" → Install → Enable

### 2. 推薦配置：MinIO (自架 S3)

**啟動 MinIO**:
```bash
docker run -d \
  -p 9000:9000 \
  -p 9001:9001 \
  --name minio \
  -e "MINIO_ROOT_USER=admin" \
  -e "MINIO_ROOT_PASSWORD=your_password" \
  -v ~/minio/data:/data \
  quay.io/minio/minio server /data --console-address ":9001"
```

**創建 Bucket**:
- 訪問 http://localhost:9001
- 登入：admin / your_password
- Buckets → Create Bucket → 名稱: `obsidian-vault`

**Remotely Save 設定**:
```
Service: S3
Endpoint: http://localhost:9000
Bucket: obsidian-vault
Access Key ID: admin
Secret Access Key: your_password
Region: us-east-1
```

### 3. 配置同步

```
Auto Sync: ON (每 5 分鐘)
Sync on Save: ON (儲存後 5 秒)

忽略規則:
- .obsidian/ (每台設備各自管理)
- .trash/
```

### 4. 從 Git 移除追蹤

```bash
# 停止追蹤（保留本地文件）
git rm --cached -r docs/obsidian-vault/

# Commit
git add .gitignore
git commit -m "chore: switch to Remotely Save for obsidian sync"
git push
```

## 其他儲存選項

### AWS S3
```
Service: S3
Region: ap-northeast-1
Bucket: your-bucket-name
Access Key: YOUR_KEY
Secret Key: YOUR_SECRET
```

### WebDAV (Nextcloud)
```
Service: WebDAV
Server: https://cloud.example.com/remote.php/dav/files/USERNAME/
Username: your_username
Password: app_password
```

### Dropbox
```
Service: Dropbox
需要 OAuth 授權
```

## 使用建議

1. **編輯前先同步**: 開啟 Obsidian → 等待自動同步 → 開始編輯
2. **關閉前確認**: 完成編輯 → 手動 Run Sync → 確認完成
3. **處理衝突**: 衝突文件會標記 `.conflict`，手動合併後刪除

## 備份策略

```bash
# 每週備份
rsync -av --delete \
  ~/Developer/ai-auto-debug-system/docs/obsidian-vault/ \
  ~/Backups/obsidian/$(date +%Y%m%d)/
```

## 參考

- [Remotely Save GitHub](https://github.com/remotely-save/remotely-save)
- [文檔](https://github.com/remotely-save/remotely-save/wiki)

---
**日期**: 2026-04-08
