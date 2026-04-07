#!/bin/bash
# ==============================================
# LogBERT 異常檢測自動化測試腳本
# 用途：產生各種異常情境，驗證 LogBERT 檢測能力
# ==============================================

set -e

echo "=== LogBERT 異常檢測測試 ==="
echo "測試時間：$(date)"
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Test counter
TESTS=0
PASSED=0

# Function to run test
run_test() {
    local test_name="$1"
    local test_command="$2"

    TESTS=$((TESTS + 1))
    echo -e "${YELLOW}[Test $TESTS]${NC} $test_name"

    # Run the command
    if eval "$test_command" 2>&1 | head -n 5; then
        echo -e "${GREEN}✓ Test executed${NC}"
    else
        echo -e "${RED}✗ Test failed to execute${NC}"
    fi
    echo ""

    # Wait a bit between tests
    sleep 2
}

# ==============================================
# Test 1: Container 突然停止
# ==============================================
run_test "Container 突然停止（immich_machine_learning）" \
    "docker stop immich_machine_learning && sleep 5 && docker start immich_machine_learning"

# ==============================================
# Test 2: 安裝不存在的套件
# ==============================================
run_test "安裝不存在的套件（產生錯誤日誌）" \
    "docker exec grafana apt-get update > /dev/null 2>&1 && docker exec grafana apt-get install -y nonexistent-package-xyz-12345 || true"

# ==============================================
# Test 3: 權限拒絕錯誤
# ==============================================
run_test "權限拒絕錯誤" \
    "docker exec grafana bash -c 'touch /root/test-file && chmod 000 /root/test-file && cat /root/test-file' || true"

# ==============================================
# Test 4: 磁碟空間錯誤模擬
# ==============================================
run_test "嘗試寫入唯讀檔案系統" \
    "docker exec loki touch /etc/read-only-test || true"

# ==============================================
# Test 5: 網路連線錯誤
# ==============================================
run_test "網路連線錯誤（連接不存在的主機）" \
    "docker exec grafana curl http://nonexistent-host-12345.local:9999/test --max-time 3 || true"

# ==============================================
# Test 6: 記憶體壓力測試（輕微）
# ==============================================
run_test "記憶體壓力測試" \
    "docker exec prometheus sh -c 'yes | head -n 1000000 > /dev/null' || true"

# ==============================================
# 等待 LogBERT 處理日誌
# ==============================================
echo -e "${YELLOW}等待 LogBERT 處理日誌...${NC}"
echo "LogBERT 每 5 分鐘檢查一次，請耐心等候"
echo "你可以用 Ctrl+C 中斷等待，然後手動檢查結果"
echo ""

# Wait time (5 minutes)
WAIT_TIME=300
echo "倒數計時：$WAIT_TIME 秒"

for i in $(seq $WAIT_TIME -1 1); do
    echo -ne "\r剩餘時間：$i 秒  "
    sleep 1
done
echo ""

# ==============================================
# 檢查檢測結果
# ==============================================
echo ""
echo "=== 檢測結果 ==="

OUTPUT_FILE=~/Developer/Grafana/logbert/output/anomalies.json

if [ -f "$OUTPUT_FILE" ]; then
    ANOMALY_COUNT=$(jq '. | length' "$OUTPUT_FILE" 2>/dev/null || echo "0")

    echo -e "${GREEN}找到異常日誌檔案${NC}"
    echo "總共檢測到 $ANOMALY_COUNT 條異常日誌"
    echo ""

    if [ "$ANOMALY_COUNT" -gt 0 ]; then
        echo "最近 10 條異常："
        jq -r '.[-10:] | .[] | "[\(.timestamp)] [\(.labels.container // "system")] \(.message[:100])"' "$OUTPUT_FILE" 2>/dev/null || echo "無法解析 JSON"
        echo ""

        echo "異常分數分布："
        jq -r '.[] | .anomaly_score' "$OUTPUT_FILE" | sort -n | uniq -c | tail -n 10
    else
        echo -e "${YELLOW}⚠️  尚未檢測到異常日誌${NC}"
        echo "可能原因："
        echo "1. LogBERT 還在初始化中"
        echo "2. 閾值設定太高（目前：0.5）"
        echo "3. 測試產生的日誌還未被處理"
    fi
else
    echo -e "${RED}✗ 找不到異常日誌檔案${NC}"
    echo "檔案路徑：$OUTPUT_FILE"
    echo ""
    echo "請檢查："
    echo "1. LogBERT 容器是否正在運行：docker ps | grep logbert"
    echo "2. LogBERT 日誌：docker logs logbert"
fi

echo ""
echo "=== 測試完成 ==="
echo "執行了 $TESTS 個測試場景"
