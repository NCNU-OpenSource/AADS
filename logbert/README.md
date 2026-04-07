# LogBERT Layer 1 實時異常檢測架構文件

## 文件資訊
- **創建日期**: 2026-03-26
- **版本**: 2.0 (Real-time Architecture)
- **作者**: LogBERT Team
- **更新記錄**: 從輪詢機制 (v1.0) 升級為實時事件驅動架構 (v2.0)

---

## 目錄
1. [架構概述](#架構概述)
2. [系統組件](#系統組件)
3. [配置設定](#配置設定)
4. [處理流程](#處理流程)
5. [核心模組](#核心模組)
6. [部署指南](#部署指南)
7. [驗證與監控](#驗證與監控)
8. [故障排除](#故障排除)

---

## 架構概述

### 整體架構圖

```
┌─────────┐
│ Docker  │ 產生日誌
│Container│
└────┬────┘
     │
     ▼
┌─────────┐
│  Alloy  │ 收集日誌 (Docker logs + systemd journal)
└────┬────┘
     │
     ▼
┌─────────┐
│  Loki   │ 日誌聚合與儲存
└────┬────┘
     │
     ├──────────────────┐
     │                  │
     ▼                  ▼
┌──────────┐      ┌──────────┐
│ Grafana  │      │ LogBERT  │ ← WebSocket 實時串流
│Dashboard │      │ (Layer 1)│
└──────────┘      └────┬─────┘
                       │
                       ▼
                  異常檢測 Pipeline
                  ├─ Drain3 模板提取
                  ├─ BERT 特徵向量化
                  ├─ 異常評分 (masked token prediction)
                  └─ 閾值過濾 (threshold=0.5)
                       │
                       ▼
                  回推 Loki (source=logbert, type=anomaly)
                       │
                       ▼
                  Grafana Dashboard 實時展示
```

### 架構演進

| 版本 | 機制 | 延遲 | 優點 | 缺點 |
|------|------|------|------|------|
| **v1.0** | 輪詢 (每 60 秒) | 0-60 秒 | 簡單穩定 | 延遲高、可能錯過瞬時異常 |
| **v2.0** | WebSocket 實時串流 | < 3 秒 | 即時響應、事件驅動 | 需要重連機制 |

**性能提升**: 延遲降低 **20 倍以上** (60 秒 → < 3 秒)

---

## 系統組件

### 1. Loki (日誌聚合系統)
- **版本**: `grafana/loki:3.6.0`
- **端口**: 3100
- **API 端點**:
  - HTTP Push: `POST /loki/api/v1/push`
  - WebSocket Tail: `WS /loki/api/v1/tail?query={...}`
  - Query API: `GET /loki/api/v1/query_range`

### 2. LogBERT (異常檢測引擎)
- **基礎鏡像**: `pytorch/pytorch:2.4.0-cuda11.8-cudnn9-runtime`
- **GPU**: NVIDIA GPU 0 (透過 Docker Compose `deploy.resources.reservations`)
- **工作目錄**: `/app`
- **輸出目錄**: `/app/output` (掛載到主機 `./logbert/output`)

### 3. 關鍵 Python 模組
| 模組 | 功能 | 檔案 |
|------|------|------|
| **loki_tail_client** | WebSocket 客戶端 | `src/loki_tail_client.py` |
| **log_buffer** | 日誌緩衝區 | `src/log_buffer.py` |
| **log_processor** | 日誌前處理 | `src/log_processor.py` |
| **anomaly_detector** | BERT 異常檢測 | `src/anomaly_detector.py` |
| **main** | 主程式邏輯 | `src/main.py` |

---

## 配置設定

### Docker Compose 環境變數

```yaml
services:
  logbert:
    environment:
      # Loki 連線
      - LOKI_URL=http://loki:3100

      # 異常檢測參數
      - ANOMALY_THRESHOLD=0.5        # 異常評分閾值 (0.0-1.0)

      # 緩衝區設定 (v2.0 新增)
      - BATCH_SIZE=50                # 批次大小 (50 條日誌)
      - MAX_WAIT_SECONDS=2.0         # 最大等待時間 (2 秒)

      # GPU 設定
      - NVIDIA_VISIBLE_DEVICES=all

    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ['0']      # 使用 GPU 0
              capabilities: [gpu]
```

### 緩衝區策略

LogBERT 使用 **雙觸發機制** 進行批次處理：

```python
# Flush 條件 (滿足其一即觸發)
1. 數量觸發: len(buffer) >= BATCH_SIZE (50)
2. 時間觸發: time_elapsed >= MAX_WAIT_SECONDS (2.0)
```

**設計原因**:
- **BATCH_SIZE=50**: 平衡 BERT 推理效率與記憶體使用
- **MAX_WAIT_SECONDS=2.0**: 確保低流量時也能及時處理

### Python 依賴 (requirements.txt)

```
requests>=2.28.0        # HTTP 請求 (推送異常回 Loki)
transformers>=4.30.0    # HuggingFace Transformers (BERT 模型)
torch>=2.0.0            # PyTorch (BERT 推理)
drain3>=0.9.11          # Drain3 日誌模板提取
numpy>=1.24.0           # 數值計算
websockets>=12.0        # WebSocket 客戶端 (v2.0 新增)
```

---

## 處理流程

### 端到端流程圖

```
[1] Log 產生
    ↓
[2] Alloy 收集 → Loki 儲存
    ↓
[3] Loki WebSocket Tail API
    ├─ 查詢: {source="docker"}
    ├─ 串流: 新日誌即時推送
    └─ 格式: {"streams": [{"stream": {...}, "values": [[timestamp, message]]}]}
    ↓
[4] LokiTailClient 接收
    ├─ WebSocket 連線
    ├─ 自動重連 (exponential backoff)
    └─ 回調: buffer.add(log)
    ↓
[5] LogBuffer 緩衝
    ├─ 收集日誌到 buffer[]
    ├─ 檢查觸發條件 (50 條 OR 2 秒)
    └─ 觸發: flush() → 執行 on_flush 回調
    ↓
[6] LogProcessor 前處理
    ├─ 提取時間戳、容器名、日誌訊息
    ├─ Drain3 模板提取: message → template
    └─ 輸出: [{"timestamp", "labels", "message", "template"}]
    ↓
[7] AnomalyDetector 檢測
    ├─ BERT Tokenization
    ├─ Masked Token Prediction
    ├─ 計算異常分數 (0.0-1.0)
    └─ 過濾: score > ANOMALY_THRESHOLD
    ↓
[8] 回推 Loki
    ├─ 標籤: {source="logbert", type="anomaly", severity="high|medium"}
    ├─ 格式: JSON {"message", "score", "template", "is_anomaly"}
    └─ API: POST /loki/api/v1/push
    ↓
[9] Grafana Dashboard 展示
    ├─ LogQL 查詢: {source="logbert", type="anomaly"}
    ├─ 變數篩選: $container, $severity
    └─ 視覺化: 時間序列、分佈圖、表格
```

### 時序圖

```
時間軸 (t)
─────────────────────────────────────────────────────────────
t=0s    Docker 產生 Log → Alloy → Loki
t=0.1s  Loki WebSocket 推送 → LogBERT buffer
t=0.2s  Docker 產生更多 Logs...
...
t=2.0s  Buffer 滿足時間條件 (2 秒) → Flush
t=2.1s  LogProcessor 處理 (Drain3 + 前處理)
t=2.3s  AnomalyDetector 檢測 (BERT 推理)
t=2.5s  異常回推 Loki
t=2.6s  Grafana Dashboard 更新
─────────────────────────────────────────────────────────────
總延遲: < 3 秒 (從 Log 產生到 Dashboard 顯示)
```

---

## 核心模組

### 1. loki_tail_client.py

**功能**: WebSocket 客戶端，訂閱 Loki 實時日誌串流

```python
class LokiTailClient:
    def __init__(self, loki_url: str, query: str, limit: int = 100):
        # Convert http:// to ws://
        ws_url = loki_url.replace("http://", "ws://").replace("https://", "wss://")
        self.url = f"{ws_url}/loki/api/v1/tail"
        self.params = {
            "query": query,       # LogQL query (e.g., {source="docker"})
            "limit": limit,       # Max entries per response
            "delay_for": 0        # Delay in seconds (0=real-time)
        }

    async def stream(self, callback: Callable[[Dict], Any]):
        """Subscribe to Loki logs with automatic reconnection"""
        reconnect_delay = 5  # Initial reconnect delay (seconds)
        max_delay = 60       # Maximum reconnect delay (seconds)

        while True:
            try:
                # Establish WebSocket connection
                async with websockets.connect(
                    self.url,
                    ping_interval=20,    # Send ping every 20 seconds
                    ping_timeout=10      # Ping timeout 10 seconds
                ) as ws:
                    print(f"✓ Connected to Loki tail: {self.url}")
                    reconnect_delay = 5  # Reset delay

                    # Continuously receive messages
                    async for message in ws:
                        data = json.loads(message)

                        # Parse Loki log format
                        for stream in data.get("streams", []):
                            labels = stream.get("stream", {})
                            for value in stream.get("values", []):
                                timestamp, log_line = value
                                await callback({
                                    "timestamp": timestamp,
                                    "labels": labels,
                                    "message": log_line
                                })

            except Exception as e:
                print(f"✗ WebSocket error: {e}, reconnecting in {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                # Exponential backoff
                reconnect_delay = min(reconnect_delay * 2, max_delay)
```

**關鍵特性**:
- ✅ 自動重連機制 (exponential backoff)
- ✅ Ping/Pong 心跳檢測 (避免靜默斷連)
- ✅ 異步處理 (asyncio)

---

### 2. log_buffer.py

**功能**: 智能日誌緩衝區，雙觸發機制

```python
class LogBuffer:
    def __init__(self, max_size: int = 50, max_wait: float = 2.0):
        self.max_size = max_size          # Size threshold
        self.max_wait = max_wait          # Time threshold (seconds)
        self.buffer: List[Dict] = []
        self.last_flush = time.time()
        self.lock = asyncio.Lock()
        self.timer_task = None
        self.on_flush = None              # Callback function

    async def add(self, log: Dict[str, Any]):
        """Add log to buffer"""
        async with self.lock:
            self.buffer.append(log)

            # Trigger condition 1: buffer full
            if len(self.buffer) >= self.max_size:
                await self.flush()

    async def flush(self):
        """Process buffered logs"""
        if not self.buffer:
            return

        logs_to_process = self.buffer.copy()
        self.buffer = []
        self.last_flush = time.time()

        # Execute callback
        if self.on_flush:
            await self.on_flush(logs_to_process)

    async def _timer_loop(self):
        """Background timer to check time-based trigger"""
        while True:
            await asyncio.sleep(0.5)  # Check every 0.5 seconds

            async with self.lock:
                # Trigger condition 2: time threshold exceeded with logs present
                if self.buffer and (time.time() - self.last_flush >= self.max_wait):
                    await self.flush()

    def start_timer(self):
        """Start background timer"""
        self.timer_task = asyncio.create_task(self._timer_loop())

    async def stop(self):
        """Stop buffer and process remaining logs"""
        if self.timer_task:
            self.timer_task.cancel()
        await self.flush()
```

**設計考量**:
- **併發安全**: 使用 `asyncio.Lock` 保護共享狀態
- **背景定時器**: 獨立 task 監控時間觸發條件
- **優雅停機**: `stop()` 確保剩餘日誌不丟失

---

### 3. main.py

**功能**: 主程式邏輯，整合所有組件

```python
async def main():
    print(f"[{datetime.now()}] Starting LogBERT Real-time Anomaly Detection Service")
    print(f"  - Loki URL: {LOKI_URL}")
    print(f"  - Batch Size: {BATCH_SIZE}")
    print(f"  - Max Wait: {MAX_WAIT_SECONDS}s")
    print(f"  - Anomaly Threshold: {ANOMALY_THRESHOLD}")

    # Initialize components
    processor = LogProcessor()
    detector = AnomalyDetector(threshold=ANOMALY_THRESHOLD)

    # Statistics
    stats = {
        "total_processed": 0,
        "total_anomalies": 0,
        "last_report": datetime.now()
    }

    # Define flush callback
    async def on_logs_flush(logs: List[Dict[str, Any]]):
        """Process a batch of logs"""
        try:
            now = datetime.now()
            print(f"\n[{now}] Processing {len(logs)} logs...")

            # 1. Preprocessing (Drain3 template extraction)
            processed = processor.process_logs(logs)
            print(f"  Processed {len(processed)} logs")

            # 2. Anomaly detection (BERT)
            results = detector.detect_anomalies(processed)
            print(f"  Analyzed {len(results)} results")

            # 3. Filter anomalies
            anomalies = [r for r in results if r["is_anomaly"]]

            # 4. Update statistics
            stats["total_processed"] += len(logs)
            stats["total_anomalies"] += len(anomalies)

            if anomalies:
                print(f"  ⚠️  Detected {len(anomalies)} anomalous logs!")

                # 5. Save to file
                output_file = OUTPUT_DIR / f"anomalies_{now.strftime('%Y%m%d_%H%M%S')}.json"
                with open(output_file, "w") as f:
                    json.dump(anomalies, f, indent=2, default=str)
                print(f"  Saved to: {output_file}")

                # 6. Push back to Loki
                print(f"  Pushing {len(anomalies)} anomalies to Loki...")
                push_anomalies_to_loki(anomalies, LOKI_URL)
                print(f"  ✓ Anomalies pushed to Loki")
            else:
                print("  ✓ No anomalies detected in this batch")

            # 7. Periodic statistics report
            if (now - stats["last_report"]).seconds >= 60:
                print(f"\n[Statistics] Total processed: {stats['total_processed']}, "
                      f"Total anomalies: {stats['total_anomalies']}")
                stats["last_report"] = now

        except Exception as e:
            print(f"  Error processing logs: {e}")
            traceback.print_exc()

    # Create buffer
    buffer = LogBuffer(max_size=BATCH_SIZE, max_wait=MAX_WAIT_SECONDS)
    buffer.on_flush = on_logs_flush
    buffer.start_timer()

    # Create Loki tail client
    client = LokiTailClient(
        loki_url=LOKI_URL,
        query='{source="docker"}',  # Monitor Docker logs
        limit=100
    )

    # Start streaming (runs forever)
    try:
        await client.stream(buffer.add)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        await buffer.stop()
        print("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
```

**處理流程總結**:
1. 接收日誌 → `buffer.add()`
2. 觸發條件 → `buffer.flush()` → `on_logs_flush()`
3. 前處理 → `processor.process_logs()` (Drain3)
4. 異常檢測 → `detector.detect_anomalies()` (BERT)
5. 過濾異常 → `score > threshold`
6. 保存 + 回推 → 檔案 + Loki

---

### 4. push_anomalies_to_loki()

**功能**: 將異常日誌回推 Loki，供 Grafana 視覺化

```python
def push_anomalies_to_loki(anomalies: List[Dict], loki_url: str):
    """Push anomalies back to Loki with special labels"""
    if not anomalies:
        return

    import requests

    streams = []
    for anomaly in anomalies:
        timestamp_ns = anomaly.get("timestamp", str(int(datetime.utcnow().timestamp() * 1e9)))
        container = anomaly.get("labels", {}).get("container", "unknown")

        # Assemble log entry
        log_entry = {
            "message": anomaly["message"],
            "score": anomaly["anomaly_score"],
            "template": anomaly.get("template", ""),
            "is_anomaly": anomaly["is_anomaly"]
        }

        # Create Loki stream
        streams.append({
            "stream": {
                "source": "logbert",            # Mark source
                "type": "anomaly",              # Mark type
                "container": container,         # Original container
                "severity": "high" if anomaly["anomaly_score"] > 0.7 else "medium"
            },
            "values": [[timestamp_ns, json.dumps(log_entry)]]
        })

    # Push in batches (100 per batch to avoid large payloads)
    batch_size = 100
    for i in range(0, len(streams), batch_size):
        batch = streams[i:i + batch_size]
        try:
            response = requests.post(
                f"{loki_url}/loki/api/v1/push",
                json={"streams": batch},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
        except Exception as e:
            print(f"  Warning: Failed to push batch {i//batch_size + 1} to Loki: {e}")
```

**Loki 標籤設計**:
- `source="logbert"`: 標記為 LogBERT 產生的異常
- `type="anomaly"`: 標記為異常類型
- `container="..."`: 保留原始容器名稱
- `severity="high|medium"`: 根據 score 分級
  - `score > 0.7`: high
  - `0.5 < score <= 0.7`: medium

---

## 部署指南

### 1. 先決條件

- ✅ Docker 與 Docker Compose
- ✅ NVIDIA GPU + nvidia-docker2 (或 Docker with NVIDIA Container Toolkit)
- ✅ 驅動版本: CUDA 11.8+

### 2. 檔案結構

```
~/Developer/Grafana/
├── docker-compose.yaml          # Main configuration
├── logbert/
│   ├── Dockerfile               # LogBERT container definition
│   ├── requirements.txt         # Python dependencies
│   ├── src/
│   │   ├── main.py              # Main program
│   │   ├── loki_tail_client.py  # WebSocket client
│   │   ├── log_buffer.py        # Log buffer
│   │   ├── log_processor.py     # Log preprocessing
│   │   └── anomaly_detector.py  # Anomaly detection
│   ├── models/                  # BERT model cache
│   └── output/                  # Anomaly output directory
├── loki/
│   └── loki-config.yaml         # Loki configuration
├── alloy/
│   └── config.alloy             # Alloy configuration
└── grafana/
    └── provisioning/
        └── dashboards/
            └── logbert-anomaly.json  # Dashboard definition
```

### 3. 部署步驟

```bash
# Navigate to project directory
cd ~/Developer/Grafana

# Build LogBERT image
docker compose build logbert

# Start all services
docker compose up -d

# View LogBERT logs
docker compose logs -f logbert

# Expected output:
# [2026-03-26 05:00:00] Starting LogBERT Real-time Anomaly Detection Service
#   - Loki URL: http://loki:3100
#   - Batch Size: 50
#   - Max Wait: 2.0s
#   - Anomaly Threshold: 0.5
#   - Mode: Real-time streaming via WebSocket
# ✓ Connected to Loki tail: ws://loki:3100/loki/api/v1/tail?query={source="docker"}
#
# [2026-03-26 05:00:05] Processing 8 logs...
#   Processed 8 logs
#   Analyzed 8 results
#   ⚠️  Detected 3 anomalous logs!
#   Saved to: /app/output/anomalies_20260326_050005.json
#   Pushing 3 anomalies to Loki...
#   ✓ Anomalies pushed to Loki
```

### 4. 驗證部署

```bash
# Check all container status
docker compose ps

# Expected output:
# NAME        STATUS    PORTS
# loki        running   0.0.0.0:3100->3100/tcp
# logbert     running   (GPU enabled)
# grafana     running   0.0.0.0:3000->3000/tcp
# prometheus  running   0.0.0.0:9090->9090/tcp
# alloy       running   0.0.0.0:12345->12345/tcp

# Test Loki connection
curl -s http://localhost:3100/ready
# Expected: ready

# Query LogBERT anomalies in Loki
curl -G -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={source="logbert",type="anomaly"}' \
  --data-urlencode 'limit=10' | jq .

# Trigger test anomaly (generate anomalous log)
docker exec grafana sh -c 'echo "CRITICAL ERROR: Database connection failed" >&2'

# Wait 3 seconds and check LogBERT logs
sleep 3
docker compose logs --tail 20 logbert
# Should see anomaly detection output
```

### 5. Grafana Dashboard 訪問

- **URL**: http://localhost:3000
- **帳號**: admin
- **密碼**: admin
- **Dashboard**: "LogBERT 異常檢測儀表板"
  - 路徑: Dashboards → LogBERT 異常檢測儀表板

**Dashboard 功能**:
- 🔴 **Layer 1 - 概覽**: 總異常數、異常率、實時異常計數
- 🟠 **Layer 2 - 分佈**: 時間趨勢、容器分佈、嚴重度分佈
- 🟡 **Layer 3 - 詳細**: Top 異常日誌、異常模板分佈、評分分佈
- 🔵 **Layer 4 - 日誌**: 完整異常日誌表格 (可搜尋、排序)

**變數篩選**:
- `$container`: 篩選特定容器
- `$severity`: 篩選嚴重度 (high / medium)

---

## 驗證與監控

### 1. 系統健康檢查

```bash
# Check Loki health status
curl http://localhost:3100/ready

# Check LogBERT container logs
docker compose logs --tail 50 logbert | grep -E "(Connected|Processing|Detected)"

# Expected to see:
# ✓ Connected to Loki tail
# [timestamp] Processing N logs...
# ⚠️ Detected M anomalous logs! (if anomalies present)
```

### 2. 延遲測試

```bash
# Send test log and record time
T1=$(date +%s.%N)
docker exec grafana sh -c 'echo "TEST_ANOMALY: Critical failure detected" >&2'

# Wait for LogBERT processing
sleep 3

# Query Grafana API to confirm anomaly display
T2=$(date +%s.%N)
curl -s -u admin:admin \
  "http://localhost:3000/api/datasources/proxy/uid/P8E80F9AEF21F6940/loki/api/v1/query_range?query={source=\"logbert\",type=\"anomaly\"}" \
  | jq '.data.result[0].values[-1]'

# Calculate latency
echo "Latency: $(echo "$T2 - $T1" | bc) seconds"
# Expected: < 3 seconds
```

### 3. 異常檢測率監控

```bash
# View statistics
docker compose logs logbert | grep Statistics

# Example output:
# [Statistics] Total processed: 1234, Total anomalies: 56
# Anomaly rate = 56 / 1234 = 4.5%
```

### 4. 輸出檔案檢查

```bash
# View latest anomaly files
ls -lh ~/Developer/Grafana/logbert/output/

# Expected to see:
# anomalies_20260326_050005.json
# anomalies_20260326_050123.json
# anomalies.json (consolidated file, keeps up to 10000 entries)

# View latest anomalies
cat ~/Developer/Grafana/logbert/output/anomalies.json | jq '.[-5:]'
```

---

## 故障排除

### 問題 1: LogBERT 無法連線到 Loki

**症狀**:
```
✗ WebSocket error: [Errno 111] Connection refused, reconnecting in 5s...
```

**原因**: Loki 尚未完全啟動

**解決方案**:
```bash
# Check Loki status
docker compose logs loki | tail -20

# Wait for Loki to fully start (see "server listening on addresses")
# Or manually restart LogBERT
docker compose restart logbert
```

---

### 問題 2: 無異常檢測輸出

**症狀**:
```
[timestamp] Processing 50 logs...
  Processed 50 logs
  Analyzed 50 results
  ✓ No anomalies detected in this batch
```

**原因**:
1. 日誌都是正常日誌 (符合預期)
2. ANOMALY_THRESHOLD 設定過高

**解決方案**:
```bash
# Solution 1: Lower threshold (adjust docker-compose.yaml)
# ANOMALY_THRESHOLD=0.5 → 0.3

# Solution 2: Generate test anomalies
docker exec grafana sh -c 'for i in {1..10}; do echo "CRITICAL ERROR $i: Unexpected failure" >&2; sleep 0.5; done'

# Wait 3 seconds and check results
sleep 3
docker compose logs --tail 30 logbert
```

---

### 問題 3: Dashboard 顯示 "No Data"

**症狀**: Grafana Dashboard 所有 panel 都顯示 "No data"

**檢查步驟**:
```bash
# 1. Confirm LogBERT anomaly data in Loki
curl -G -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={source="logbert"}' \
  --data-urlencode 'limit=1' | jq .

# If returns empty result: [] → no anomaly data

# 2. Check Grafana datasource UID
curl -s -u admin:admin http://localhost:3000/api/datasources | jq '.[] | select(.type=="loki") | {name, uid}'

# Expected output:
# {
#   "name": "Loki",
#   "uid": "P8E80F9AEF21F6940"
# }

# 3. Confirm Dashboard datasource.uid matches
cat ~/Developer/Grafana/grafana/provisioning/dashboards/logbert-anomaly.json | jq '.panels[0].datasource.uid'

# Expected output: "P8E80F9AEF21F6940"
```

**解決方案**:
- 如果 UID 不一致 → 更新 Dashboard JSON 中的所有 `datasource.uid`
- 如果無異常資料 → 參考問題 2

---

### 問題 4: BERT 模型下載失敗

**症狀**:
```
OSError: Can't load config for 'bert-base-uncased'. Make sure that:
- 'bert-base-uncased' is a correct model identifier
```

**原因**: 網路問題或 HuggingFace Hub 無法訪問

**解決方案**:
```bash
# Solution 1: Manually download model to models/ directory
mkdir -p ~/Developer/Grafana/logbert/models
cd ~/Developer/Grafana/logbert/models

# Use git-lfs to download
git lfs install
git clone https://huggingface.co/bert-base-uncased

# Solution 2: Use mirror site (for mainland China)
# Modify model path in anomaly_detector.py:
# BertModel.from_pretrained('bert-base-uncased')
# → BertModel.from_pretrained('bert-base-uncased', mirror='https://hf-mirror.com')
```

---

### 問題 5: GPU 不可用

**症狀**:
```
RuntimeError: CUDA not available or no CUDA-capable device found
```

**檢查步驟**:
```bash
# 1. Confirm host GPU available
nvidia-smi

# 2. Confirm Docker can access GPU
docker run --rm --gpus all nvidia/cuda:11.8.0-base-ubuntu22.04 nvidia-smi

# 3. Check LogBERT container GPU settings
docker inspect logbert | jq '.[0].HostConfig.DeviceRequests'
```

**解決方案**:
- 安裝 nvidia-docker2: `sudo apt install nvidia-docker2`
- 重啟 Docker: `sudo systemctl restart docker`
- 確認 docker-compose.yaml 中的 `deploy.resources.reservations.devices` 配置正確

---

## 附錄

### A. 關鍵 LogQL 查詢

```logql
# 1. Query all LogBERT anomalies
{source="logbert", type="anomaly"}

# 2. Query specific container anomalies
{source="logbert", type="anomaly", container="grafana"}

# 3. Query high-severity anomalies
{source="logbert", type="anomaly", severity="high"}

# 4. Query anomalies containing specific keywords
{source="logbert", type="anomaly"} |~ "ERROR|CRITICAL"

# 5. Count anomalies per minute
sum(count_over_time({source="logbert", type="anomaly"}[1m]))

# 6. Count by container
sum by (container) (count_over_time({source="logbert", type="anomaly"}[5m]))
```

### B. 性能調優參數

| 參數 | 預設值 | 調整建議 | 影響 |
|------|--------|---------|------|
| `BATCH_SIZE` | 50 | 10-100 | 數值越大，BERT 推理越高效，但延遲略增 |
| `MAX_WAIT_SECONDS` | 2.0 | 1.0-5.0 | 數值越小，延遲越低，但處理批次越小 |
| `ANOMALY_THRESHOLD` | 0.5 | 0.3-0.7 | 數值越低，檢測越敏感，誤報率越高 |

**推薦配置**:
- **低延遲優先**: `BATCH_SIZE=20`, `MAX_WAIT_SECONDS=1.0`
- **高效能優先**: `BATCH_SIZE=100`, `MAX_WAIT_SECONDS=5.0`
- **平衡模式**: `BATCH_SIZE=50`, `MAX_WAIT_SECONDS=2.0` (預設)

### C. 相關資源

- [Loki HTTP API 文件](https://grafana.com/docs/loki/latest/reference/loki-http-api/)
- [Loki Tail API 範例](https://grafana.com/blog/2019/08/13/lokis-path-to-ga-live-tailing/)
- [Python websockets 文件](https://websockets.readthedocs.io/)
- [HuggingFace Transformers 文件](https://huggingface.co/docs/transformers/)
- [Drain3 GitHub](https://github.com/logpai/Drain3)

---

## 版本歷史

| 版本 | 日期 | 變更內容 |
|------|------|---------|
| v1.0 | 2026-03-20 | 初始版本 (輪詢機制) |
| v2.0 | 2026-03-26 | 升級為實時架構 (WebSocket) |

---

**文件結束**

如有問題或建議，請聯繫：LogBERT Team
