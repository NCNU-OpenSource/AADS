#!/usr/bin/env bash
set -euo pipefail

MODE="${AADS_DB_MIGRATION_MODE:-safe}"
DB_CONTAINER="${AADS_DB_CONTAINER:-timescaledb}"
DB_USER="${DB_USER:-logdb}"
DB_NAME="${DB_NAME:-logdb}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "$MODE" == "destructive" ]]; then
  echo "Applying destructive lab migration..."
  docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -c '
    DROP TABLE IF EXISTS audit_events, execution_steps, idempotency_records,
      agent_tasks, plan_executions, plan_approvals, node_locks, agent_nodes,
      diagnosis_reports, anomaly_logs, raw_logs, knowledge_cases CASCADE;
  '
elif [[ "$MODE" != "safe" ]]; then
  echo "AADS_DB_MIGRATION_MODE must be safe or destructive" >&2
  exit 1
fi

for file in "$ROOT"/layer0-storage/timescaledb/migrations/*.sql; do
  echo "Applying $file"
  docker exec -i "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 < "$file"
done
