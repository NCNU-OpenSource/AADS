#!/usr/bin/env bash
set -euo pipefail

missing=0
for cmd in multipass ssh scp openssl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required command: $cmd" >&2
    missing=1
  fi
done

if ! command -v orb >/dev/null 2>&1 && ! pgrep -f OrbStack >/dev/null 2>&1; then
  echo "warning: OrbStack was not detected; macOS Docker debugging should use OrbStack." >&2
fi

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "Prerequisites look usable."
