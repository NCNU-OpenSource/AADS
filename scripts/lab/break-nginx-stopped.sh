#!/usr/bin/env bash
set -euo pipefail

TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes${SSH_KEY:+ -i $SSH_KEY}"

# shellcheck disable=SC2086
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" sudo bash -lc \
  'systemctl stop nginx; logger -t nginx "nginx failed to start: service stopped by AADS lab"; printf "%s [error] nginx failed to start: service stopped by AADS lab\n" "$(date -Is)" >> /var/log/nginx/error.log'
echo "nginx stopped on ${TARGET_IP}"
