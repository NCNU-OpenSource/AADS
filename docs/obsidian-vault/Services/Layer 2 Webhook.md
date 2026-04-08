---
service: layer2-webhook
layer: Layer 2
type: webhook
port: 8080
---

# Layer 2 Webhook

## Overview

Layer 2 Webhook 是 Fan-out 架構的 **Sink B**，負責接收 Grafana Alloy 轉發的異常並**即時觸發** LLM 分析流程。

## Architecture Role

```
LogBERT 偵測異常
    ↓
POST to Alloy :9999
    ↓
Fan-out Sink B: Layer 2 Webhook :8080
    ↓
Background Task: 即時 LLM 分析
    ↓
生成診斷報告 & 發送通知
```

## Why Webhook?

### 傳統方式（輪詢）❌

```python
while True:
    anomalies = db.query("SELECT * FROM anomaly_logs WHERE processed = FALSE")
    for anomaly in anomalies:
        analyze(anomaly)
    time.sleep(10)  # 延遲 10 秒
```

**缺點**：
- 延遲高（最多 10 秒）
- 浪費資源（持續查詢資料庫）
- 無法即時響應

### Webhook 方式（事件驅動）✅

```python
@app.post("/api/webhooks/anomaly")
async def receive_anomaly_webhook(payload, background_tasks):
    background_tasks.add_task(analyze, payload)
    return {"status": "accepted"}  # 立即返回
```

**優點**：
- **零延遲**：異常發生時立即觸發
- **資源高效**：無需輪詢
- **可擴展**：使用 background tasks

## API Endpoints

### POST /api/webhooks/anomaly

接收 Alloy 轉發的異常，觸發即時分析。

**Request Body**:
```json
{
  "timestamp": "2026-04-08T10:30:00Z",
  "service": "api-gateway",
  "log_message": "Connection timeout after 30s",
  "logbert_anomaly_score": 0.95,
  "level": "ERROR"
}
```

**Response** (立即返回):
```json
{
  "status": "accepted",
  "message": "Anomaly queued for analysis",
  "timestamp": "2026-04-08T10:30:00Z"
}
```

**Background Processing**:
1. 轉換為內部 anomaly 格式
2. 加入 background task queue
3. 執行 `process_anomaly_batch([anomaly])`
4. 聚類 → 指標關聯 → 知識庫檢索 → LLM 推理
5. 生成診斷報告
6. 發送通知（Slack/Webhook）

### GET /health

健康檢查端點。

**Response**:
```json
{
  "status": "healthy",
  "service": "layer2-analyzer"
}
```

## Configuration

### Environment Variables

同 Layer 2 Analyzer 的環境變數配置。

### Docker Compose

```yaml
layer2-analyzer:
  build: ./layer2-analyzer
  container_name: layer2-analyzer
  ports:
    - "8080:8080"  # Webhook port
  environment:
    # ... 其他環境變數 ...
  networks:
    - observability
```

## Architecture

### Dual-Mode Operation

Layer 2 Analyzer 同時運行兩個服務：

1. **Analyzer Loop** (原有)：從資料庫輪詢異常
2. **API Server** (新增)：Webhook 即時接收異常

```python
async def main():
    # 創建兩個並行任務
    analyzer_task = asyncio.create_task(run_analyzer())
    api_task = asyncio.create_task(run_api_server())
    
    # 同時執行
    await asyncio.gather(analyzer_task, api_task)
```

### Why Both?

| 模式 | 用途 | 優點 |
|------|------|------|
| **Webhook** | 即時異常（來自 Alloy） | 零延遲觸發 |
| **Polling** | 歷史異常、補償機制 | 確保不遺漏 |

## Performance

### Background Tasks

使用 FastAPI 的 `BackgroundTasks` 避免阻塞 webhook 響應：

```python
@app.post("/api/webhooks/anomaly")
async def receive_anomaly_webhook(
    payload: AnomalyWebhookPayload,
    background_tasks: BackgroundTasks
):
    # 立即返回（不阻塞）
    background_tasks.add_task(
        analyzer_instance.process_anomaly_batch,
        [anomaly]
    )
    return {"status": "accepted"}
```

**優勢**：
- Webhook 響應時間 < 100ms
- 分析在背景執行，不影響接收速度
- 避免 Alloy 等待超時

### Async Processing

完整的異步流程：

```
Webhook 接收 (async)
    ↓
Background Task (async)
    ↓
process_anomaly_batch (async)
    ↓
LLM 推理 (async aiohttp)
    ↓
DB 寫入 (async asyncpg)
```

## Monitoring

### Health Check

```bash
curl http://localhost:8080/health
```

### Test Webhook

```bash
curl -X POST http://localhost:8080/api/webhooks/anomaly \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-04-08T10:30:00Z",
    "service": "test-service",
    "log_message": "Test anomaly for webhook",
    "logbert_anomaly_score": 0.99,
    "level": "ERROR"
  }'
```

### Logs

```bash
docker compose logs -f layer2-analyzer | grep webhook
```

## Implementation

### 檔案位置

- `layer2-analyzer/src/main.py` - FastAPI webhook 實作

### 關鍵代碼

#### FastAPI App

```python
app = FastAPI(title="Layer 2 Analyzer API")
analyzer_instance: RootCauseAnalyzer = None

@app.on_event("startup")
async def startup_event():
    global analyzer_instance
    analyzer_instance = RootCauseAnalyzer()
    await analyzer_instance.init_db_pool()
```

#### Webhook Endpoint

```python
@app.post("/api/webhooks/anomaly")
async def receive_anomaly_webhook(
    payload: AnomalyWebhookPayload,
    background_tasks: BackgroundTasks
):
    anomaly = {
        'timestamp': datetime.fromisoformat(payload.timestamp),
        'service': payload.service,
        'log_message': payload.log_message,
        'logbert_anomaly_score': payload.logbert_anomaly_score,
        'level': payload.level,
    }
    
    background_tasks.add_task(
        analyzer_instance.process_anomaly_batch,
        [anomaly]
    )
    
    return {"status": "accepted"}
```

#### Dual-Mode Main

```python
async def main():
    analyzer_task = asyncio.create_task(run_analyzer())
    api_task = asyncio.create_task(run_api_server())
    await asyncio.gather(analyzer_task, api_task)
```

## Deployment

### 建立映像

```bash
cd layer2-analyzer
docker build -t layer2-analyzer:latest .
```

### 啟動服務

```bash
docker compose up -d layer2-analyzer
```

### 檢查日誌

```bash
docker compose logs -f layer2-analyzer
```

## Troubleshooting

### Webhook 404

**問題**: Alloy 報告 404 錯誤

**檢查**:
```bash
docker compose ps layer2-analyzer
curl http://localhost:8080/health
```

**解決**: 確保 port 8080 已暴露，服務已啟動

### Background Task 不執行

**問題**: Webhook 返回 accepted 但沒有分析

**檢查日誌**:
```bash
docker compose logs layer2-analyzer | grep "process_anomaly_batch"
```

**可能原因**:
- Analyzer 未初始化
- Database pool 連線失敗
- LLM API 錯誤

## Related

- [[Services/FastAPI Ingester]]
- [[Layer 2 - Root Cause Analysis]]
- [[Services/LLM Reasoner]]
- [[Infrastructure/Grafana Alloy]]
- [[ADR/ADR-002-Event-Driven-Log-Collection]]
- [[Debug-Log/2026-04-08-System-Integration-Debug]]
