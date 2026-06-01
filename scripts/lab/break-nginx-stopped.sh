#!/usr/bin/env bash
set -euo pipefail

TARGET="${AADS_TARGET_VM:-aads-target}"
multipass exec "$TARGET" -- sudo bash -lc 'systemctl stop nginx; logger -t nginx "nginx failed to start: service stopped by AADS lab"; printf "%s [error] nginx failed to start: service stopped by AADS lab\n" "$(date -Is)" >> /var/log/nginx/error.log'
echo "nginx stopped on $TARGET"
