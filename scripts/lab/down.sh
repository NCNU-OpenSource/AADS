#!/usr/bin/env bash
set -euo pipefail

CONTROLLER="${AADS_CONTROLLER_VM:-aads-controller}"
TARGET="${AADS_TARGET_VM:-aads-target}"

for vm in "$CONTROLLER" "$TARGET"; do
  if multipass info "$vm" >/dev/null 2>&1; then
    multipass stop "$vm" || true
  fi
done

echo "Stopped AADS lab VMs. Delete them with: multipass delete --purge $CONTROLLER $TARGET"
