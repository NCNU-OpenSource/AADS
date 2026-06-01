#!/bin/bash
# ADDS Obsidian Sync Hook
# Syncs code changes to Obsidian vault documentation

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
VAULT_PATH="$PROJECT_ROOT/docs/obsidian-vault"
HOOK_INPUT=$(cat)

# Parse the hook input to get tool info
TOOL_NAME=$(echo "$HOOK_INPUT" | jq -r '.tool_name // empty' 2>/dev/null || echo "")
FILE_PATH=$(echo "$HOOK_INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null || echo "")

# Only check for relevant file changes
if [[ -z "$FILE_PATH" ]] || [[ "$FILE_PATH" == *"obsidian-vault"* ]] || [[ "$FILE_PATH" == *".claude"* ]]; then
    echo "$HOOK_INPUT"
    exit 0
fi

# Map source files to Obsidian docs
get_related_docs() {
    local file="$1"
    local docs=""

    case "$file" in
        */layer0-collector/alloy/*) docs="Infrastructure/Grafana Alloy.md" ;;
        */layer0-collector/loki/*) docs="Infrastructure/Loki.md" ;;
        */layer0-storage/timescaledb/*) docs="Infrastructure/TimescaleDB.md Database/Database Schema.md" ;;
        */layer0-storage/sync/*) docs="Infrastructure/Log Archiver.md" ;;
        */layer1-filter/*) docs="Services/Layer 1 Filter.md" ;;
        */logbert/*) docs="Services/LogBERT.md" ;;
        */layer2-analyzer/src/anomaly_consumer*) docs="Services/Anomaly Consumer.md" ;;
        */layer2-analyzer/src/root_cause_analyzer/anomaly_aggregator*) docs="Services/Anomaly Aggregator.md" ;;
        */layer2-analyzer/src/root_cause_analyzer/metrics_correlator*) docs="Services/Metrics Correlator.md" ;;
        */layer2-analyzer/src/root_cause_analyzer/knowledge_base*) docs="Services/Knowledge Base.md" ;;
        */layer2-analyzer/src/root_cause_analyzer/llm_reasoner*) docs="Services/LLM Reasoner.md" ;;
        */layer2-analyzer/src/suggestion_generator*) docs="Services/Suggestion Generator.md" ;;
        */layer2-analyzer/src/notification_hub*) docs="Services/Notification Hub.md" ;;
        */layer2-analyzer/src/llm/*) docs="External/OpenAI API.md External/Ollama.md" ;;
        */layer3-remediation/*) docs="Services/Auto Remediation.md" ;;
        */dashboard/*) docs="Visualization/Dashboard.md" ;;
        */grafana/*) docs="Visualization/Grafana Dashboard.md" ;;
        */prometheus/*) docs="Infrastructure/Prometheus.md" ;;
        */docker-compose.yaml) docs="Docker Compose Services.md" ;;
    esac

    echo "$docs"
}

RELATED_DOCS=$(get_related_docs "$FILE_PATH")

if [[ -n "$RELATED_DOCS" ]]; then
    # Log reminder to stderr (visible to user)
    echo "" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "📚 OBSIDIAN SYNC REMINDER" >&2
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
    echo "Modified: ${FILE_PATH#$PROJECT_ROOT/}" >&2
    echo "" >&2
    echo "Related docs to update:" >&2
    for doc in $RELATED_DOCS; do
        echo "  → $doc" >&2
    done
    echo "" >&2

    # Check if Obsidian CLI is available
    if command -v obsidian &> /dev/null; then
        echo "💡 Use: obsidian open file=\"<doc_name>\"" >&2
    fi
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" >&2
fi

# Pass through the original input
echo "$HOOK_INPUT"
