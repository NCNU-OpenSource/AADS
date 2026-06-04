#!/usr/bin/env bash
# Build the On-Device Agent payload tarball (aads-agent.tgz).
#
# The tarball contains the full pi-agent/ tree (src, wrappers, systemd,
# install/install.sh, alloy/config.alloy.tmpl, requirements.txt). It is the
# artifact that dist/install-agent.sh downloads and unpacks on the target.
#
# Reused by:
#   - dist/bootstrap-server.sh  (places it in dist/ so the dashboard can serve it)
#   - .github/workflows/build-images.yml  (uploads it as a release asset)
#
# Usage:
#   dist/build-agent-payload.sh [OUTPUT]
#     OUTPUT  destination path (default: <repo>/dist/aads-agent.tgz)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT/dist/aads-agent.tgz}"

if [[ ! -d "$ROOT/pi-agent" ]]; then
  echo "ERROR: $ROOT/pi-agent not found — run from a full repo checkout." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

# Pack pi-agent/ but drop editor/OS cruft and Python caches so the payload is
# clean and reproducible. COPYFILE_DISABLE stops macOS tar from adding ._* files.
COPYFILE_DISABLE=1 tar \
  --exclude='._*' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  -C "$ROOT" -czf "$OUTPUT" pi-agent

echo "Built agent payload: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
