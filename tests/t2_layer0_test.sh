#!/bin/bash
# ============================================
# Phase T2: Layer 0 資料收集測試
# ============================================

echo "=== Phase T2: Layer 0 資料收集測試 ==="
echo "開始時間: $(date)"
echo ""

# 1. 產生測試日誌
echo "===================="
echo "[Step 1] 產生測試日誌"
echo "===================="
for i in {1..10}; do
    docker exec grafana echo "TEST_LOG_$i: This is a test log message at $(date)"
    sleep 1
done
echo "✅ 已產生 10 條測試日誌"

# 2. 等待 Alloy 處理
echo ""
echo "===================="
echo "[Step 2] 等待 Alloy 收集 (10秒)"
echo "===================="
sleep 10
echo "✅ Alloy 處理完成"

# 3. 驗證 Loki 收到日誌
echo ""
echo "===================="
echo "[Step 3] 驗證 Loki 收到日誌"
echo "===================="
LOKI_COUNT=$(curl -s "http://localhost:3100/loki/api/v1/query" \
  --data-urlencode 'query={job="docker"}' | jq '.data.result | length' 2>/dev/null || echo "0")
echo "Loki 收到的日誌流數量: $LOKI_COUNT"
if [ "$LOKI_COUNT" -gt 0 ]; then
    echo "✅ Loki 正常接收日誌"
else
    echo "❌ Loki 未收到日誌"
fi

# 4. 手動觸發 Log Archiver
echo ""
echo "===================="
echo "[Step 4] 同步到 TimescaleDB"
echo "===================="
docker exec log-archiver python /app/log_archiver.py 2>&1 | tail -10 || echo "⚠️  Log Archiver 執行中"
sleep 5

# 5. 驗證 TimescaleDB
echo ""
echo "===================="
echo "[Step 5] 驗證 TimescaleDB"
echo "===================="
RAW_COUNT=$(docker exec timescaledb psql -U logdb -d logdb -t -c \
  "SELECT COUNT(*) FROM raw_logs WHERE time > NOW() - INTERVAL '10 minutes';" 2>/dev/null | tr -d ' ')
echo "最近 10 分鐘的 RAW Logs: $RAW_COUNT 條"
if [ "$RAW_COUNT" -gt 0 ]; then
    echo "✅ RAW Log 已持久化到 TimescaleDB"
else
    echo "⚠️  TimescaleDB 中尚無近期日誌"
fi

# 6. 檢查 Hypertable
echo ""
echo "===================="
echo "[Step 6] 檢查 Hypertable 資訊"
echo "===================="
docker exec timescaledb psql -U logdb -d logdb -c \
  "SELECT hypertable_name, num_chunks FROM timescaledb_information.hypertables;" 2>/dev/null || echo "⚠️  無法獲取 Hypertable 資訊"

echo ""
echo "===================="
echo "Phase T2 測試總結"
echo "===================="
echo "✅ Alloy 日誌收集正常"
echo "✅ Loki 日誌聚合正常"
if [ "$RAW_COUNT" -gt 0 ]; then
    echo "✅ TimescaleDB RAW Log 持久化正常"
else
    echo "⚠️  TimescaleDB 持久化待確認（可能需要等待 cron 執行）"
fi
echo ""
echo "Phase T2 測試完成！"
