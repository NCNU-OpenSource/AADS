#!/usr/bin/env bash
set -euo pipefail

TARGET_IP="${AADS_TARGET_IP:?AADS_TARGET_IP must be set}"
SSH_USER="${AADS_SSH_USER:-ubuntu}"
SSH_KEY="${AADS_SSH_KEY:-}"
SSH_OPTS="-o StrictHostKeyChecking=no -o BatchMode=yes${SSH_KEY:+ -i $SSH_KEY}"

# shellcheck disable=SC2086
ssh $SSH_OPTS "${SSH_USER}@${TARGET_IP}" sudo bash -lc \
  'cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.aads-bak && echo "broken {" >> /etc/nginx/nginx.conf && systemctl reload nginx || true; printf "%s [emerg] nginx: [emerg] invalid number of arguments in lab broken config\n" "$(date -Is)" >> /var/log/nginx/error.log'
echo "nginx bad config injected on ${TARGET_IP}"
