#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER="${AADS_CONTROLLER_VM:-aads-controller}"
ARCHIVE="$(mktemp /tmp/aads-controller.XXXXXX.tgz)"

cleanup() {
  rm -f "$ARCHIVE"
}
trap cleanup EXIT

COPYFILE_DISABLE=1 tar \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='.aads-lab-token' \
  --exclude='.aads-lab-admin-key' \
  --exclude='docs/obsidian-vault' \
  --exclude='__pycache__' \
  --exclude='.pytest_cache' \
  --exclude='.mypy_cache' \
  --exclude='.ruff_cache' \
  --exclude='models' \
  --exclude='._*' \
  -C "$ROOT" \
  -czf "$ARCHIVE" .

multipass exec "$CONTROLLER" -- bash -lc 'rm -rf ~/AADS && mkdir -p ~/AADS'
multipass transfer "$ARCHIVE" "$CONTROLLER:/tmp/aads-controller.tgz"
multipass exec "$CONTROLLER" -- bash -lc 'tar -xzf /tmp/aads-controller.tgz -C ~/AADS && find ~/AADS -name "._*" -delete'

echo "Controller repository synced to $CONTROLLER:~/AADS"
