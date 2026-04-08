---
service: ingester
layer: Layer 1
type: api-server
port: 8000
---

# FastAPI Ingester

## Overview

FastAPI Ingester 是 Fan-out 架構的 **Sink A**，負責接收 Grafana Alloy 轉發的異常資料並寫入 PostgreSQL。

## Architecture Role

```
LogBERT 偵測異常
    ↓
POST to Alloy :9999
    ↓
Fan-out Sink A: Ingester :8000
    ↓
Write to PostgreSQL anomaly_logs 表
```

## API Endpoints

### POST /api/anomalies

接收單筆異常日誌。

**Request Body**:
```json
{
  "timestamp": "2026-04-08T10:30:00Z",
  "service": "api-gateway",
  "log_message": "Connection timeout",
  "logbert_anomaly_score": 0.95,
  "level": "ERROR",
  "is_anomaly": true
}
```

**Response**:
```json
{
  "id": 12345,
  "message": "Anomaly log created successfully",
  "timestamp": "2026-04-08T10:30:00Z"
}
```

### POST /api/anomalies/batch

接收批次異常日誌。

**Request Body**:
```json
{
  "logs": [
    {
      "timestamp": "2026-04-08T10:30:00Z",
      "service": "api-gateway",
      "log_message": "Connection timeout",
      "logbert_anomaly_score": 0.95,
      "level": "ERROR"
    },
    ...
  ]
}
```

**Response**:
```json
{
  "message": "Batch of 10 anomaly logs created successfully",
  "count": 10
}
```

### GET /health

健康檢查端點。

**Response**:
```json
{
  "status": "healthy",
  "database": "connected",
  "timestamp": "2026-04-08T10:30:00Z"
}
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | timescaledb | PostgreSQL 主機 |
| `DB_PORT` | 5432 | PostgreSQL 端口 |
| `DB_NAME` | logdb | 資料庫名稱 |
| `DB_USER` | logdb | 資料庫使用者 |
| `DB_PASSWORD` | logdb_password | 資料庫密碼 |

### Docker Compose

```yaml
ingester:
  build: ./layer1-filter/ingester
  container_name: ingester
  ports:
    - "8000:8000"
  environment:
    - DB_HOST=timescaledb
    - DB_PORT=5432
    - DB_NAME=logdb
    - DB_USER=logdb
    - DB_PASSWORD=${TIMESCALEDB_PASSWORD:-logdb_password}
  depends_on:
    timescaledb:
      condition: service_healthy
  networks:
    - observability
  restart: unless-stopped
```

## Database Schema

寫入的表格：`anomaly_logs`

```sql
CREATE TABLE anomaly_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL,
    service VARCHAR(255) NOT NULL,
    log_message TEXT NOT NULL,
    logbert_anomaly_score FLOAT NOT NULL,
    level VARCHAR(50) DEFAULT 'INFO',
    is_anomaly BOOLEAN DEFAULT TRUE
);
```

## Performance

- **Connection Pool**: 5-20 connections
- **Batch Insert**: 支援批次寫入提升效能
- **Transaction**: 批次寫入使用 transaction 確保一致性

## Monitoring

### Health Check

```bash
curl http://localhost:8000/health
```

### Test Insert

```bash
curl -X POST http://localhost:8000/api/anomalies \
  -H "Content-Type: application/json" \
  -d '{
    "timestamp": "2026-04-08T10:30:00Z",
    "service": "test-service",
    "log_message": "Test anomaly",
    "logbert_anomaly_score": 0.99,
    "level": "ERROR"
  }'
```

## Implementation

### 檔案位置

- `layer1-filter/ingester/main.py` - FastAPI 應用程式
- `layer1-filter/ingester/Dockerfile` - 容器化配置
- `layer1-filter/ingester/requirements.txt` - Python 依賴

### 關鍵代碼

```python
@app.post("/api/anomalies", status_code=status.HTTP_201_CREATED)
async def create_anomaly(log: AnomalyLog):
    async with db_pool.acquire() as conn:
        query = """
            INSERT INTO anomaly_logs
            (timestamp, service, log_message, logbert_anomaly_score, level, is_anomaly)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
        """
        
        log_id = await conn.fetchval(
            query,
            log.timestamp,
            log.service,
            log.log_message,
            log.logbert_anomaly_score,
            log.level,
            log.is_anomaly,
        )
        
        return {"id": log_id, "message": "Anomaly log created successfully"}
```

## Deployment

### 建立映像

```bash
cd layer1-filter/ingester
docker build -t ingester:latest .
```

### 啟動服務

```bash
docker compose up -d ingester
```

### 檢查日誌

```bash
docker compose logs -f ingester
```

## Related

- [[Layer 1 - Anomaly Filtering]]
- [[Services/Layer 2 Webhook]]
- [[Infrastructure/Grafana Alloy]]
- [[Database/Database Schema]]
- [[ADR/ADR-002-Event-Driven-Log-Collection]]
- [[Debug-Log/2026-04-08-System-Integration-Debug]]
