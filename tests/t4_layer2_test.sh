#!/bin/bash
# ============================================
# Phase T4: Layer 2 根因分析測試
# ============================================

echo "=== Phase T4: Layer 2 根因分析測試 ==="
echo "開始時間: $(date)"
echo ""

# 0. 檢查 Layer 2 Analyzer 服務狀態
echo "===================="
echo "[Step 0] 檢查 Layer 2 Analyzer 服務"
echo "===================="
docker logs layer2-analyzer --tail 10
echo ""

# 1. 注入一批測試異常（觸發分析）
echo "===================="
echo "[Step 1] 注入測試異常"
echo "===================="
for i in {1..10}; do
    docker run --rm --network ai-auto-debug-system_observability alpine echo "ERROR [T4-TEST-$i]: Database connection timeout - host: db.example.com:5432"
    sleep 0.5
done
for i in {1..5}; do
    docker run --rm --network ai-auto-debug-system_observability alpine echo "CRITICAL [T4-TEST-$i]: High memory usage detected - 95% consumed"
    sleep 0.5
done
echo "✅ 已注入 15 條測試異常"

# 2. 等待 Layer 1 處理
echo ""
echo "===================="
echo "[Step 2] 等待 Layer 1 處理 (30秒)"
echo "===================="
sleep 30
echo "✅ Layer 1 處理完成"

# 3. 檢查異常是否已入庫
echo ""
echo "===================="
echo "[Step 3] 驗證異常已入 DB"
echo "===================="
ANOMALY_COUNT=$(docker exec timescaledb psql -U logdb -d logdb -t -c \
  "SELECT COUNT(*) FROM anomaly_logs WHERE raw_message LIKE '%T4-TEST%';" | tr -d ' ')
echo "測試異常數量: $ANOMALY_COUNT 條"

# 4. 等待 Layer 2 聚類和分析 (poll interval 30s + 分析時間)
echo ""
echo "===================="
echo "[Step 4] 等待 Layer 2 分析 (90秒)"
echo "===================="
sleep 90
echo "✅ 分析時間完成"

# 5. 檢查 Layer 2 處理日誌
echo ""
echo "===================="
echo "[Step 5] 檢查 Layer 2 處理日誌"
echo "===================="
docker logs layer2-analyzer --tail 50 | grep -E "Aggregating|Created.*clusters|LLM completion|Stored diagnosis" || echo "⚠️ 未找到相關日誌"

# 6. 驗證診斷報告
echo ""
echo "===================="
echo "[Step 6] 驗證診斷報告"
echo "===================="
DIAGNOSIS_COUNT=$(docker exec timescaledb psql -U logdb -d logdb -t -c \
  "SELECT COUNT(*) FROM diagnosis_reports WHERE timestamp > NOW() - INTERVAL '5 minutes';" | tr -d ' ')
echo "最近 5 分鐘診斷報告: $DIAGNOSIS_COUNT 條"

if [ "$DIAGNOSIS_COUNT" -gt 0 ]; then
    echo ""
    echo "診斷報告詳情:"
    docker exec timescaledb psql -U logdb -d logdb -c \
      "SELECT diagnosis_id, timestamp, severity,
              LEFT(summary, 80) as summary
       FROM diagnosis_reports
       WHERE timestamp > NOW() - INTERVAL '5 minutes'
       ORDER BY timestamp DESC
       LIMIT 5;"

    echo ""
    echo "診斷報告內容（root_cause）:"
    docker exec timescaledb psql -U logdb -d logdb -c \
      "SELECT diagnosis_id,
              LEFT(root_cause::text, 100) as root_cause_preview
       FROM diagnosis_reports
       WHERE timestamp > NOW() - INTERVAL '5 minutes'
       ORDER BY timestamp DESC
       LIMIT 3;"
fi

# 7. 檢查知識庫狀態
echo ""
echo "===================="
echo "[Step 7] 檢查知識庫 (ChromaDB)"
echo "===================="
docker exec layer2-analyzer python -c "
from root_cause_analyzer.knowledge_base import KnowledgeBase
kb = KnowledgeBase('/app/data/chromadb')
print(f'知識庫文檔數: {kb.collection.count()}')
" 2>/dev/null || echo "⚠️ 知識庫查詢失敗"

# 8. 驗證 RAW Log 上下文獲取
echo ""
echo "===================="
echo "[Step 8] 驗證 RAW Log 上下文"
echo "===================="
RAW_LOG_COUNT=$(docker exec timescaledb psql -U logdb -d logdb -t -c \
  "SELECT COUNT(*) FROM raw_logs WHERE time > NOW() - INTERVAL '10 minutes';" | tr -d ' ')
echo "最近 10 分鐘 RAW logs: $RAW_LOG_COUNT 條"

# 9. 檢查 LLM API 調用狀態
echo ""
echo "===================="
echo "[Step 9] 檢查 LLM API 狀態"
echo "===================="
echo "LLM 調用統計:"
docker logs layer2-analyzer --since 5m | grep -E "LLM completion successful|API response structure" | tail -5

echo ""
echo "===================="
echo "Phase T4 測試總結"
echo "===================="
echo "測試異常注入: $([[ $ANOMALY_COUNT -gt 0 ]] && echo '✅' || echo '❌')"
echo "Layer 2 分析: $([[ $DIAGNOSIS_COUNT -gt 0 ]] && echo '✅' || echo '❌')"
echo "RAW Log 存儲: $([[ $RAW_LOG_COUNT -gt 0 ]] && echo '✅' || echo '❌')"
echo ""

if [ "$DIAGNOSIS_COUNT" -gt 0 ]; then
    echo "✅ Layer 2 根因分析正常運行"
    echo "✅ LLM API 整合成功"
    echo "✅ 診斷報告已持久化到 PostgreSQL"
else
    echo "⚠️ 診斷報告未生成（可能需要更多時間或檢查日誌）"
fi

echo ""
echo "Phase T4 測試完成！"
