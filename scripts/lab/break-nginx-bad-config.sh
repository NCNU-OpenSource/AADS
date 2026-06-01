#!/usr/bin/env bash
set -euo pipefail

TARGET="${AADS_TARGET_VM:-aads-target}"
multipass exec "$TARGET" -- sudo bash -lc 'cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.aads-bak && echo "broken {" >> /etc/nginx/nginx.conf && systemctl reload nginx || true; printf "%s [emerg] nginx: [emerg] invalid number of arguments in lab broken config\n" "$(date -Is)" >> /var/log/nginx/error.log'
echo "nginx bad config injected on $TARGET"
