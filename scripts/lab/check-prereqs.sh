#!/usr/bin/env bash
set -euo pipefail

# PVE environment: only ssh/scp/openssl needed (no multipass/OrbStack).
missing=0
for cmd in ssh scp openssl; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing required command: $cmd" >&2
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

if [[ -z "${AADS_CONTROLLER_IP:-}" ]] && [[ -z "${AADS_TARGET_IP:-}" ]]; then
  echo "warning: AADS_CONTROLLER_IP and AADS_TARGET_IP are not set; set them before running deploy scripts." >&2
fi

echo "Prerequisites look usable."
