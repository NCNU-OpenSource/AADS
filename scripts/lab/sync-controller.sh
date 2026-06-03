#!/usr/bin/env bash
# Sync the AADS repo to the controller VM via SSH/SCP.
# PVE environment: set AADS_CONTROLLER_IP before running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER_IP="${AADS_CONTROLLER_IP:?AADS_CONTROLLER_IP must be set (e.g. 192.168.1.10)}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes"
ARCHIVE="$(mktemp /tmp/aads-controller.XXXXXX.tgz)"

cleanup() { rm -f "$ARCHIVE"; }
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
  --exclude='.venv-test' \
  --exclude='models' \
  --exclude='._*' \
  -C "$ROOT" \
  -czf "$ARCHIVE" .

ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" 'sudo rm -rf ~/AADS && mkdir -p ~/AADS'
scp $SSH_OPTS "$ARCHIVE" "${SSH_USER}@${CONTROLLER_IP}:/tmp/aads-controller.tgz"
ssh $SSH_OPTS "${SSH_USER}@${CONTROLLER_IP}" \
  'tar -xzf /tmp/aads-controller.tgz -C ~/AADS && find ~/AADS -name "._*" -delete && rm /tmp/aads-controller.tgz'

echo "Synced to controller ${CONTROLLER_IP}"
