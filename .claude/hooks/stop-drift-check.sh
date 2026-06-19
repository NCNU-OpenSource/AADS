#!/bin/bash
# ADDS Architecture Drift Check
# Runs on session stop to verify no architectural drift

PROJECT_ROOT="/home/bs10081/Developer/ai-auto-debug-system"
VAULT_PATH="$PROJECT_ROOT/docs/obsidian-vault"
CANVAS_FILE="$VAULT_PATH/Architecture-Overview.canvas"

echo "" >&2
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
echo "🔍 ARCHITECTURE DRIFT CHECK" >&2
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2

DRIFT_FOUND=0

# Check 1: Layer directories match documentation
echo "" >&2
echo "📁 Layer Structure:" >&2

EXPECTED_LAYERS=("layer0-collector" "layer0-storage" "layer1-filter" "layer2-analyzer")
for layer in "${EXPECTED_LAYERS[@]}"; do
    if [[ -d "$PROJECT_ROOT/$layer" ]]; then
        echo "  ✓ $layer exists" >&2
    else
        echo "  ⚠ $layer MISSING" >&2
        DRIFT_FOUND=1
    fi
done

# Check 2: Core services exist
echo "" >&2
echo "🔧 Core Services:" >&2

declare -A SERVICES=(
    ["layer1-filter/src/main.py"]="Layer 1 Filter"
    ["logbert/src/anomaly_detector.py"]="LogBERT"
    ["layer2-analyzer/src/main.py"]="Layer 2 Analyzer"
    ["layer2-analyzer/src/root_cause_analyzer/llm_reasoner.py"]="LLM Reasoner"
    ["dashboard/app.py"]="Dashboard"
)

for path in "${!SERVICES[@]}"; do
    if [[ -f "$PROJECT_ROOT/$path" ]]; then
        echo "  ✓ ${SERVICES[$path]}" >&2
    else
        echo "  ⚠ ${SERVICES[$path]} MISSING ($path)" >&2
        DRIFT_FOUND=1
    fi
done

# Check 3: Documentation completeness
echo "" >&2
echo "📚 Documentation:" >&2

DOC_COUNT=$(find "$VAULT_PATH" -name "*.md" | wc -l)
CANVAS_EXISTS=$([[ -f "$CANVAS_FILE" ]] && echo "yes" || echo "no")

echo "  • Markdown files: $DOC_COUNT" >&2
echo "  • Architecture canvas: $CANVAS_EXISTS" >&2

if [[ $DOC_COUNT -lt 25 ]]; then
    echo "  ⚠ Documentation may be incomplete (expected 25+ files)" >&2
    DRIFT_FOUND=1
fi

# Check 4: Recent changes without doc updates
echo "" >&2
echo "📝 Recent Changes:" >&2

# Find Python files modified in last hour
RECENT_PY=$(find "$PROJECT_ROOT" -name "*.py" -mmin -60 \
    -not -path "*/.git/*" \
    -not -path "*/test*" \
    -not -path "*/__pycache__/*" 2>/dev/null | wc -l)

RECENT_DOCS=$(find "$VAULT_PATH" -name "*.md" -mmin -60 2>/dev/null | wc -l)

echo "  • Python files modified (last hour): $RECENT_PY" >&2
echo "  • Docs modified (last hour): $RECENT_DOCS" >&2

if [[ $RECENT_PY -gt 0 ]] && [[ $RECENT_DOCS -eq 0 ]]; then
    echo "  ⚠ Code changed but docs not updated!" >&2
    DRIFT_FOUND=1
fi

# Summary
echo "" >&2
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
if [[ $DRIFT_FOUND -eq 0 ]]; then
    echo "✅ No architectural drift detected" >&2
else
    echo "⚠️  Potential drift detected - please review" >&2
    echo "" >&2
    echo "Run the following to sync:" >&2
    echo "  /obsidian-sync" >&2
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
