#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PATTERN='spokenly|ask_user_dictation|mcp__spokenly'

ACTIVE_FILES=(
  "$ROOT/AGENTS.md"
  "$ROOT/.codex/config.toml"
  "$ROOT/.codex/hooks.json"
  "$ROOT/.claude/CLAUDE.md"
  "$ROOT/.claude/settings.json"
  "$HOME/.codex/config.toml"
  "$HOME/.Codex/config.toml"
  "$HOME/.claude/CLAUDE.md"
  "$HOME/.claude/settings.json"
)

while IFS= read -r file; do
  ACTIVE_FILES+=("$file")
done < <(
  find "$ROOT" -maxdepth 1 -type f -name '*.md' \
    ! -name '*.bak' \
    ! -path "$ROOT/docs/obsidian-vault/*" \
    ! -path "$ROOT/.git/*"
)

BACKUP_FILES=(
  "$HOME/.codex/config.toml.bak"
  "$HOME/.Codex/config.toml.bak"
  "$HOME/.claude/CLAUDE.md.bak"
  "$HOME/.claude/settings.json.bak"
)

failed=0
for file in "${ACTIVE_FILES[@]}"; do
  [[ -f "$file" ]] || continue
  if grep -Eiq "$PATTERN" "$file"; then
    echo "ERROR: active Spokenly reference found in $file" >&2
    grep -Ein "$PATTERN" "$file" >&2 || true
    failed=1
  fi
done

for file in "${BACKUP_FILES[@]}"; do
  [[ -f "$file" ]] || continue
  if grep -Eiq "$PATTERN" "$file"; then
    echo "NOTE: stale backup Spokenly reference remains in $file (not modified)." >&2
  fi
done

exit "$failed"
