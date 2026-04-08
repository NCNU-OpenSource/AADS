---
title: Deployment Verification
type: guide
tags: [deployment, verification, testing]
created: 2026-04-08
---

# 部署驗證指南

## 概述

部署前驗證腳本確保所有配置正確，避免部署失敗。

## 驗證腳本

**位置**: `scripts/verify-deployment.sh`

**執行方式**:
```bash
bash scripts/verify-deployment.sh
```

## 驗證項目

### 1. 配置驗證 (Configuration Validation)

- ✅ **Alloy 配置語法**: 使用官方容器驗證 config.alloy
- ✅ **Docker Compose 配置**: 確認 YAML 格式正確

### 2. 必要文件檢查 (Required Files)

確認以下文件存在：

**Layer 0 配置**:
- `layer0-collector/alloy/config.alloy`
- `layer0-collector/logrotate/layer0-logrotate.conf`
- `layer0-collector/auditd/auditd-tuned.conf`
- `layer0-collector/auditd/audit.rules`

**TimescaleDB Migrations**:
- `001_create_hypertables.sql`
- `002_compression_policy.sql`
- `003_retention_policy.sql`

**Layer 1 & 2 服務**:
- `layer1-filter/ingester/main.py` & `Dockerfile`
- `layer2-analyzer/src/main.py` & `Dockerfile`

### 3. Port 配置檢查 (Port Mappings)

| 服務 | Port | 用途 |
|------|------|------|
| Ingester | 8000 | FastAPI 異常接收 |
| Layer 2 Webhook | 8080 | 即時分析觸發 |
| Grafana | 3000 | 可視化儀表板 |
| Loki | 3100 | 日誌查詢 API |
| Prometheus | 9090 | Metrics 查詢 |
| Dashboard | 5000 | Flask Web UI |

### 4. Dockerfile EXPOSE 檢查

確保 Dockerfile 中正確聲明 port：

```dockerfile
# layer1-filter/ingester/Dockerfile
EXPOSE 8000

# layer2-analyzer/Dockerfile
EXPOSE 8080
```

### 5. Volume 掛載檢查 (Volume Mounts)

| 掛載路徑 | 用途 | 模式 |
|---------|------|------|
| `/var/log:/var/log` | 系統日誌讀取 | ro |
| `/proc:/host/proc` | 程序資訊讀取 | ro |
| `migrations:/docker-entrypoint-initdb.d` | 資料庫初始化 | ro |

### 6. Prometheus 配置檢查

確認 Remote Write Receiver 已啟用：

```yaml
command:
  - '--web.enable-remote-write-receiver'
```

### 7. Alloy 配置細節檢查

- ✅ **WAL 語法**: 不包含已棄用的 `dir` 屬性
- ✅ **Hostname**: 使用 `constants.hostname` 而非 `env()`
- ✅ **Process 監控**: `procfs_path = "/host/proc"` 已設定
- ✅ **Process Matcher**: cmdline matcher 已配置
- ✅ **Fan-out 架構**: `loki.source.api` + 雙 sink 配置

## 驗證通過標準

所有檢查項目顯示 `✓` 綠色勾號。

## 驗證失敗處理

### 常見錯誤

#### 1. Alloy 配置驗證失敗

**錯誤訊息**:
```
Error: unrecognized attribute name "dir"
```

**解決方案**: 檢查 [[Debug-Log/2026-04-08-System-Integration-Debug#P0-1 修復 Alloy WAL 語法|WAL 語法修復]]

#### 2. Port 未映射

**錯誤訊息**:
```
Port 8080 mapped (Layer 2 Webhook) ✗
```

**解決方案**: 在 `docker-compose.yaml` 中添加：
```yaml
layer2-analyzer:
  ports:
    - "8080:8080"
```

#### 3. Volume 未掛載

**錯誤訊息**:
```
/proc mounted (for prometheus.exporter.process) ✗
```

**解決方案**: 在 Alloy 服務中添加：
```yaml
volumes:
  - /proc:/host/proc:ro
```

## 部署流程

驗證通過後執行以下步驟：

### 1. 啟動服務

```bash
docker compose up -d
```

### 2. 檢查日誌

```bash
# 查看所有服務日誌
docker compose logs -f

# 查看特定服務
docker compose logs -f layer2-analyzer
docker compose logs -f ingester
```

### 3. 健康檢查

```bash
# Ingester
curl http://localhost:8000/health
# 預期輸出: {"status":"healthy","database":"connected"}

# Layer 2 Webhook
curl http://localhost:8080/health
# 預期輸出: {"status":"healthy","service":"layer2-analyzer"}
```

### 4. 訪問 UI

- **Grafana**: http://localhost:3000 (admin/admin)
- **Dashboard**: http://localhost:5000
- **Prometheus**: http://localhost:9090

## 持續驗證

建議在以下情況重新執行驗證：

- 修改 Alloy 配置後
- 更新 docker-compose.yaml 後
- 添加新服務後
- 系統升級後

## 相關文檔

- [[Debug-Log/2026-04-08-System-Integration-Debug]] - 系統整合問題修復記錄
- [[System Flow]] - 完整系統流程
- [[Services/FastAPI Ingester]] - Ingester 服務文檔
- [[Services/Layer 2 Webhook]] - Webhook 服務文檔

## 自動化建議

未來可整合到 CI/CD pipeline：

```yaml
# .github/workflows/verify.yml
name: Verify Deployment
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run verification
        run: bash scripts/verify-deployment.sh
```

---

**最後更新**: 2026-04-08  
**狀態**: ✅ 所有檢查通過
