# AI Auto Debug System - Codex 規則

## Obsidian 文檔同步

**重要規則**: 每次修改 `docs/obsidian-vault/` 目錄下的任何檔案後，**必須**執行同步腳本將變更上傳到 MinIO。

### 執行方式

```bash
sync-obsidian
```

或直接執行：
```bash
python3 ~/.Codex/skills/sync-obsidian
```

### 何時需要同步

- ✅ 新增 Obsidian 文檔（.md 或 .canvas 文件）
- ✅ 修改現有 Obsidian 文檔
- ✅ 刪除 Obsidian 文檔
- ✅ 更新技術報告 (Technical-Reports/)
- ✅ 更新 ADR 文檔 (ADR/)
- ✅ 更新架構圖 (Architecture-Overview.canvas)

### 同步檢查清單

在完成任何涉及 Obsidian 的工作後：

1. [ ] 確認所有文檔修改已儲存
2. [ ] 執行 `sync-obsidian`
3. [ ] 檢查同步輸出，確認無錯誤
4. [ ] 驗證上傳檔案數量合理

### MinIO 連線資訊

- **Endpoint**: https://s3.tfbs.site
- **Bucket**: obsidian-aads
- **Console**: https://console.tfbs.site

### 技術細節

同步腳本會：
- 掃描 `docs/obsidian-vault/` 目錄
- 上傳所有 `.md`、`.canvas`、`.png`、`.jpg` 文件
- 跳過 `.obsidian/` 配置目錄
- 保持目錄結構
- 顯示上傳統計

---

## 專案特定規則

### 文檔結構

Obsidian vault 組織方式：
```
docs/obsidian-vault/
├── ADR/                    # Architecture Decision Records
├── Technical-Reports/      # 實作進度與技術報告
├── Research/              # 研究筆記
├── Infrastructure/        # 基礎設施文檔
├── Services/             # 服務元件文檔
├── Database/             # 資料庫 Schema
└── Architecture-Overview.canvas  # 系統架構圖
```

### 文檔更新原則

1. **實作完成後立即更新文檔**
   - 新功能 → 更新對應的 Service 文檔
   - Bug 修復 → 記錄在 Technical-Reports
   - 架構變更 → 更新 Architecture-Overview.canvas + 寫 ADR

2. **測試問題必須記錄**
   - 遇到的問題、根本原因、解決方案
   - 記錄在 Technical-Reports 或 Troubleshooting

3. **重大決策必須寫 ADR**
   - 技術選型（如 Pydantic vs JSON Schema）
   - 架構變更（如 Map-Reduce 引入）
   - API 設計決策

---

**最後更新**: 2026-04-08
