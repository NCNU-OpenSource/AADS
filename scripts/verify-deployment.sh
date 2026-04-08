#!/bin/bash
# ============================================
# Deployment Verification Script
# 驗證 AI Auto Debug System 部署是否正確
# ============================================

set -e

echo "=========================================="
echo "AI Auto Debug System - Deployment Verification"
echo "=========================================="
echo ""

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check function
check() {
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $1"
        return 0
    else
        echo -e "${RED}✗${NC} $1"
        return 1
    fi
}

# ============================================
# 1. Configuration Validation
# ============================================
echo "1. Validating Configurations..."
echo ""

# Alloy config validation
echo -n "  Checking Alloy config syntax... "
docker run --rm \
    -v $(pwd)/layer0-collector/alloy:/config \
    grafana/alloy:latest validate /config/config.alloy > /dev/null 2>&1
check "Alloy configuration valid"

# Docker Compose validation
echo -n "  Checking Docker Compose config... "
docker compose config --quiet > /dev/null 2>&1
check "Docker Compose configuration valid"

echo ""

# ============================================
# 2. Required Files Check
# ============================================
echo "2. Checking Required Files..."
echo ""

files=(
    "layer0-collector/alloy/config.alloy"
    "layer0-collector/logrotate/layer0-logrotate.conf"
    "layer0-collector/auditd/auditd-tuned.conf"
    "layer0-collector/auditd/audit.rules"
    "layer0-storage/timescaledb/migrations/001_create_hypertables.sql"
    "layer0-storage/timescaledb/migrations/002_compression_policy.sql"
    "layer0-storage/timescaledb/migrations/003_retention_policy.sql"
    "layer1-filter/ingester/main.py"
    "layer1-filter/ingester/Dockerfile"
    "layer2-analyzer/src/main.py"
    "layer2-analyzer/Dockerfile"
)

for file in "${files[@]}"; do
    echo -n "  Checking $file... "
    [ -f "$file" ]
    check "$file exists"
done

echo ""

# ============================================
# 3. Port Configuration Check
# ============================================
echo "3. Checking Port Configurations..."
echo ""

echo -n "  Checking Ingester port 8000... "
grep -q "8000:8000" docker-compose.yaml
check "Port 8000 mapped (Ingester)"

echo -n "  Checking Layer 2 Webhook port 8080... "
grep -q "8080:8080" docker-compose.yaml
check "Port 8080 mapped (Layer 2 Webhook)"

echo -n "  Checking Grafana port 3000... "
grep -q "3000:3000" docker-compose.yaml
check "Port 3000 mapped (Grafana)"

echo -n "  Checking Loki port 3100... "
grep -q "3100:3100" docker-compose.yaml
check "Port 3100 mapped (Loki)"

echo -n "  Checking Prometheus port 9090... "
grep -q "9090:9090" docker-compose.yaml
check "Port 9090 mapped (Prometheus)"

echo -n "  Checking Dashboard port 5000... "
grep -q "5000:5000" docker-compose.yaml
check "Port 5000 mapped (Dashboard)"

echo ""

# ============================================
# 4. Dockerfile EXPOSE Check
# ============================================
echo "4. Checking Dockerfile EXPOSE..."
echo ""

echo -n "  Checking Ingester Dockerfile... "
grep -q "EXPOSE 8000" layer1-filter/ingester/Dockerfile
check "Ingester exposes port 8000"

echo -n "  Checking Layer 2 Dockerfile... "
grep -q "EXPOSE 8080" layer2-analyzer/Dockerfile
check "Layer 2 Analyzer exposes port 8080"

echo ""

# ============================================
# 5. Volume Mount Check
# ============================================
echo "5. Checking Volume Mounts..."
echo ""

echo -n "  Checking /var/log mount... "
grep -q "/var/log:/var/log:ro" docker-compose.yaml
check "/var/log mounted (for loki.source.file)"

echo -n "  Checking /proc mount... "
grep -q "/proc:/host/proc:ro" docker-compose.yaml
check "/proc mounted (for prometheus.exporter.process)"

echo -n "  Checking TimescaleDB migrations... "
grep -q "migrations:/docker-entrypoint-initdb.d" docker-compose.yaml
check "Migrations directory mounted"

echo ""

# ============================================
# 6. Prometheus Remote Write Check
# ============================================
echo "6. Checking Prometheus Configuration..."
echo ""

echo -n "  Checking remote write receiver... "
grep -q "web.enable-remote-write-receiver" docker-compose.yaml
check "Remote write receiver enabled"

echo ""

# ============================================
# 7. Alloy Configuration Details
# ============================================
echo "7. Checking Alloy Configuration Details..."
echo ""

echo -n "  Checking WAL syntax (no 'dir' attribute)... "
! grep -q 'dir.*=.*"/tmp' layer0-collector/alloy/config.alloy
check "WAL syntax correct (no 'dir' attribute)"

echo -n "  Checking constants.hostname usage... "
grep -q "constants.hostname" layer0-collector/alloy/config.alloy
check "Using constants.hostname (not env())"

echo -n "  Checking procfs_path configuration... "
grep -q 'procfs_path.*=.*"/host/proc"' layer0-collector/alloy/config.alloy
check "procfs_path set to /host/proc"

echo -n "  Checking process matcher... "
grep -q 'matcher {' layer0-collector/alloy/config.alloy && \
grep -q 'cmdline' layer0-collector/alloy/config.alloy
check "Process matcher configured"

echo -n "  Checking fan-out configuration... "
grep -q "loki.source.api" layer0-collector/alloy/config.alloy && \
grep -q "postgres_ingester" layer0-collector/alloy/config.alloy && \
grep -q "llm_webhook" layer0-collector/alloy/config.alloy
check "Fan-out architecture configured"

echo ""

# ============================================
# Summary
# ============================================
echo "=========================================="
echo "Verification Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Start services: docker compose up -d"
echo "  2. Check logs: docker compose logs -f"
echo "  3. Verify health:"
echo "     - curl http://localhost:8000/health"
echo "     - curl http://localhost:8080/health"
echo "  4. Access Grafana: http://localhost:3000 (admin/admin)"
echo "  5. Access Dashboard: http://localhost:5000"
echo ""
