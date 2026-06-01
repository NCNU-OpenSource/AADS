#!/bin/bash
# Obsidian Sync Check Hook
# Checks if code changes require Obsidian documentation updates

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VAULT_PATH="$PROJECT_ROOT/docs/obsidian-vault"
ARCHITECTURE_CANVAS="$VAULT_PATH/Architecture-Overview.canvas"

# Get recently modified files (last 10 minutes)
MODIFIED_FILES=$(find "$PROJECT_ROOT" -type f \
    -not -path "*/.git/*" \
    -not -path "*/docs/obsidian-vault/*" \
    -not -path "*/.claude/*" \
    -not -path "*/__pycache__/*" \
    -mmin -10 \
    -name "*.py" -o -name "*.yaml" -o -name "*.yml" -o -name "*.sql" 2>/dev/null)

if [ -z "$MODIFIED_FILES" ]; then
    exit 0
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📚 OBSIDIAN SYNC CHECK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Check which components were modified
COMPONENTS_TO_UPDATE=""

for file in $MODIFIED_FILES; do
    relative_path="${file#$PROJECT_ROOT/}"

    case "$relative_path" in
        layer0-collector/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 0 collector: Grafana Alloy.md, Loki.md"
            ;;
        layer0-storage/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 0 storage: TimescaleDB.md, Log Archiver.md, Database Schema.md"
            ;;
        layer1-filter/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 1: Layer 1 Filter.md"
            ;;
        logbert/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 1: LogBERT.md"
            ;;
        layer2-analyzer/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 2: LLM Reasoner.md, Anomaly Consumer.md, etc."
            ;;
        layer3-remediation/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Layer 3: Auto Remediation.md, Notification Hub.md"
            ;;
        dashboard/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Frontend: Dashboard.md"
            ;;
        grafana/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Visualization: Grafana Dashboard.md"
            ;;
        prometheus/*)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Infrastructure: Prometheus.md"
            ;;
        docker-compose.yaml)
            COMPONENTS_TO_UPDATE="$COMPONENTS_TO_UPDATE\n  - Reference: Docker Compose Services.md"
            ;;
    esac
done

if [ -n "$COMPONENTS_TO_UPDATE" ]; then
    echo ""
    echo "⚠️  Code changes detected that may require Obsidian updates:"
    echo ""
    echo -e "$COMPONENTS_TO_UPDATE" | sort -u
    echo ""
    echo "📋 Checklist:"
    echo "  [ ] Update affected Obsidian documentation"
    echo "  [ ] Verify changes align with Architecture-Overview.canvas"
    echo "  [ ] Update API Endpoints.md if APIs changed"
    echo "  [ ] Update Configuration Files.md if configs changed"
    echo ""
fi

# Check for architectural drift
echo "🔍 Architecture Consistency Check:"
echo ""

# Count layers in code vs docs
CODE_LAYERS=$(find "$PROJECT_ROOT" -maxdepth 1 -type d -name "layer*" | wc -l)
DOC_LAYERS=$(ls "$VAULT_PATH"/Layer*.md 2>/dev/null | wc -l)

if [ "$CODE_LAYERS" -ne "$DOC_LAYERS" ]; then
    echo "  ⚠️  Layer count mismatch: Code has $CODE_LAYERS, Docs have $DOC_LAYERS"
else
    echo "  ✓ Layer count matches ($CODE_LAYERS layers)"
fi

# Check if new Python files have corresponding docs
NEW_SERVICES=$(find "$PROJECT_ROOT" -path "*/.git" -prune -o -type f -name "*.py" -mmin -10 -print 2>/dev/null | grep -v __pycache__ | grep -v test)
if [ -n "$NEW_SERVICES" ]; then
    echo "  ℹ️  New/modified Python files - verify documentation exists"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
