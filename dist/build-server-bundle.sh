#!/usr/bin/env bash
# Build the AADS server bundle (aads-server.tgz).
#
# Contains everything dist/install-server.sh needs to bring up the full
# Docker Compose stack on a fresh host — config files, migrations, compose
# override, .env template, and the dist/ helper scripts.
#
# Reused by:
#   - dist/install-server.sh  (standalone curl|bash bootstrap)
#   - .github/workflows/build-images.yml  (uploads as a release asset)
#
# Usage:
#   dist/build-server-bundle.sh [OUTPUT]
#     OUTPUT  destination path (default: <repo>/dist/aads-server.tgz)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:-$ROOT/dist/aads-server.tgz}"

mkdir -p "$(dirname "$OUTPUT")"

# Build the agent payload first so it gets bundled into the server package
# (the dashboard serves it for self-hosted installs).
bash "$ROOT/dist/build-agent-payload.sh" "$ROOT/dist/aads-agent.tgz"

COPYFILE_DISABLE=1 tar \
  --exclude='._*' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.DS_Store' \
  -C "$ROOT" -czf "$OUTPUT" \
  docker-compose.prod.yaml \
  .env.example \
  dist/bootstrap-server.sh \
  dist/build-agent-payload.sh \
  dist/build-server-bundle.sh \
  dist/install-agent.sh \
  dist/install-server.sh \
  dist/aads-agent.tgz \
  litellm/config.yaml \
  prometheus/prometheus.yaml \
  grafana/provisioning \
  layer0-collector/alloy/config.alloy \
  layer0-collector/loki/loki-config.yaml \
  layer0-storage/timescaledb/migrations \
  pi-agent

echo "Built server bundle: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
