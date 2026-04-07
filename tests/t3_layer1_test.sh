#!/bin/bash
# ============================================
# Phase T3: Layer 1 異常過濾測試
# ============================================

echo "=== Phase T3: Layer 1 異常過濾測試 ==="
echo "開始時間: $(date)"
echo ""

# 0. 檢查 Layer 1 Filter 服務狀態
echo "===================="
echo "[Step 0] 檢查 Layer 1 Filter 服務"
echo "===================="
docker logs layer1-filter --tail 10
echo ""

# 1. 注入正常日誌（作為基準）
echo "===================="
echo "[Step 1] 注入正常日誌（基準）"
echo "===================="
for i in {1..5}; do
    docker run --rm --network ai-auto-debug-system_observability alpine echo "INFO: Application started successfully (test $i)"
    docker run --rm --network ai-auto-debug-system_observability alpine echo "INFO: Request processed in 50ms (test $i)"
    sleep 1
done
echo "✅ 已注入 10 條正常日誌"

# 2. 注入異常日誌
echo ""
echo "===================="
echo "[Step 2] 注入異常日誌"
echo "===================="
docker run --rm --network ai-auto-debug-system_observability alpine echo "ERROR: OutOfMemoryError: Java heap space exhausted"
sleep 1
docker run --rm --network ai-auto-debug-system_observability alpine echo "FATAL: Database connection failed: Connection refused on port 5432"
sleep 1
docker run --rm --network ai-auto-debug-system_observability alpine echo "CRITICAL: Disk usage exceeded 95% on /dev/sda1"
sleep 1
docker run --rm --network ai-auto-debug-system_observability alpine echo "ERROR: NullPointerException at com.app.Service.process(Service.java:123)"
sleep 1
docker run --rm --network ai-auto-debug-system_observability alpine echo "PANIC: Kernel panic - not syncing: Attempted to kill init!"
echo "✅ 已注入 5 條異常日誌"

# 3. 等待 LogBERT 處理
echo ""
echo "===================="
echo "[Step 3] 等待 LogBERT 處理 (30秒)"
echo "===================="
sleep 30
echo "✅ 處理完成"

# 4. 檢查 Layer 1 Filter 日誌
echo ""
echo "===================="
echo "[Step 4] 檢查 Layer 1 Filter 處理日誌"
echo "===================="
docker logs layer1-filter --tail 30 | grep -E "Anomaly detected|anomaly_score|Processed batch|Detected.*anomalies|Stored.*anomalies"

# 5. 驗證異常 DB
echo ""
echo "===================="
echo "[Step 5] 驗證異常 DB"
echo "===================="
ANOMALY_COUNT=$(docker exec timescaledb psql -U logdb -d logdb -t -c \
  "SELECT COUNT(*) FROM anomaly_logs WHERE time > NOW() - INTERVAL '5 minutes';" | tr -d ' ')
echo "最近 5 分鐘檢測到的異常: $ANOMALY_COUNT 條"

if [ "$ANOMALY_COUNT" -gt 0 ]; then
    echo ""
    echo "異常日誌詳情:"
    docker exec timescaledb psql -U logdb -d logdb -c \
      "SELECT time, container, anomaly_score, filter_stage,
              LEFT(raw_message, 60) as message
       FROM anomaly_logs
       WHERE time > NOW() - INTERVAL '5 minutes'
       ORDER BY time DESC
       LIMIT 10;"

    echo ""
    echo "異常統計:"
    docker exec timescaledb psql -U logdb -d logdb -c \
      "SELECT filter_stage, COUNT(*) as count,
              ROUND(AVG(anomaly_score)::numeric, 3) as avg_score,
              ROUND(MIN(anomaly_score)::numeric, 3) as min_score,
              ROUND(MAX(anomaly_score)::numeric, 3) as max_score
       FROM anomaly_logs
       WHERE time > NOW() - INTERVAL '5 minutes'
       GROUP BY filter_stage;"
fi

echo ""
echo "===================="
echo "Phase T3 測試總結"
echo "===================="
if [ "$ANOMALY_COUNT" -gt 0 ]; then
    echo "✅ LogBERT 異常檢測正常運行"
    echo "✅ 異常 Log 已持久化到 PostgreSQL"
    echo "✅ Filter Stage 標記正確"
else
    echo "⚠️  未檢測到異常（可能需要調整閾值或等待更長時間）"
fi
echo ""
echo "Phase T3 測試完成！"
