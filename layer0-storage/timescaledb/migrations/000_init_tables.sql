-- AADS canonical schema for Layer 0-4 single-node Ubuntu runtime.
-- Fresh lab/dev deployments may recreate the database volume. Production
-- deployments should apply additive migrations with AADS_DB_MIGRATION_MODE=safe.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS raw_logs (
    id BIGSERIAL,
    time TIMESTAMPTZ NOT NULL,
    node_id TEXT NOT NULL DEFAULT 'controller',
    container TEXT,
    service TEXT,
    compose_project TEXT,
    source TEXT,
    message TEXT,
    labels JSONB DEFAULT '{}'::jsonb
);

SELECT create_hypertable('raw_logs', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_raw_logs_node_time ON raw_logs(node_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_container ON raw_logs(container, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_service ON raw_logs(service, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_project ON raw_logs(compose_project, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_source ON raw_logs(source);
CREATE INDEX IF NOT EXISTS idx_raw_logs_labels ON raw_logs USING GIN(labels);

-- Keep anomaly_logs relational in v1. It needs a true UNIQUE(dedup_key) for
-- first-write-wins plan triggering; Timescale hypertables require unique
-- indexes to include the time partition column, which would break that
-- contract.
CREATE TABLE IF NOT EXISTS anomaly_logs (
    id BIGSERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL,
    node_id TEXT NOT NULL DEFAULT 'controller',
    container TEXT,
    service TEXT,
    compose_project TEXT,
    raw_message TEXT,
    template TEXT,
    anomaly_score DOUBLE PRECISION,
    filter_stage TEXT,
    filter_metadata JSONB DEFAULT '{}'::jsonb,
    dedup_key TEXT UNIQUE,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_confirmed BOOLEAN DEFAULT NULL,
    labels JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_anomaly_time ON anomaly_logs(time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_node_time ON anomaly_logs(node_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_container ON anomaly_logs(container);
CREATE INDEX IF NOT EXISTS idx_anomaly_score ON anomaly_logs(anomaly_score DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_filter_stage ON anomaly_logs(filter_stage);
CREATE INDEX IF NOT EXISTS idx_anomaly_dedup_key ON anomaly_logs(dedup_key);

CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id BIGSERIAL PRIMARY KEY,
    diagnosis_id TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    severity TEXT,
    summary TEXT,
    root_cause JSONB DEFAULT '{}'::jsonb,
    affected_services JSONB DEFAULT '[]'::jsonb,
    correlated_metrics JSONB DEFAULT '{}'::jsonb,
    recommended_actions JSONB DEFAULT '[]'::jsonb,
    action_plan JSONB,
    schema_version TEXT NOT NULL DEFAULT '1.0',
    plan_status TEXT NOT NULL DEFAULT 'pending_approval',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diagnosis_timestamp ON diagnosis_reports(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_diagnosis_severity ON diagnosis_reports(severity);
CREATE INDEX IF NOT EXISTS idx_diagnosis_plan_status ON diagnosis_reports(plan_status);

CREATE TABLE IF NOT EXISTS agent_nodes (
    node_id TEXT PRIMARY KEY,
    environment TEXT NOT NULL DEFAULT 'test',
    agent_version TEXT NOT NULL DEFAULT 'unknown',
    base_url TEXT NOT NULL,
    -- V2: argv runner capabilities (replaces legacy catalog supported_commands).
    runner_capabilities JSONB DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'registered',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS plan_approvals (
    id BIGSERIAL PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES diagnosis_reports(diagnosis_id) ON DELETE CASCADE,
    actor TEXT NOT NULL DEFAULT 'admin',
    decision TEXT NOT NULL,
    approved_until TIMESTAMPTZ,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_plan_approvals_plan_created ON plan_approvals(plan_id, created_at DESC);

CREATE TABLE IF NOT EXISTS plan_executions (
    execution_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES diagnosis_reports(diagnosis_id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    target_node_id TEXT,
    schema_version TEXT NOT NULL DEFAULT '2.0',
    requested_by TEXT NOT NULL DEFAULT 'admin',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    retry_count INTEGER NOT NULL DEFAULT 0,
    result JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_plan_executions_status ON plan_executions(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_plan_executions_plan ON plan_executions(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_executions_idempotency_key ON plan_executions(idempotency_key);

CREATE TABLE IF NOT EXISTS idempotency_records (
    idempotency_key TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    plan_id TEXT,
    task_id TEXT,
    execution_id TEXT REFERENCES plan_executions(execution_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    CHECK (
        (scope = 'plan_execute' AND plan_id IS NOT NULL AND execution_id IS NOT NULL)
        OR
        (scope = 'agent_task' AND task_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_idempotency_records_expires ON idempotency_records(expires_at);

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL REFERENCES agent_nodes(node_id) ON DELETE CASCADE,
    task_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    schema_version TEXT NOT NULL DEFAULT '1.0',
    requested_by TEXT NOT NULL DEFAULT 'system-agent',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    result JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agent_tasks_node_status ON agent_tasks(node_id, status, requested_at);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_idempotency_key ON agent_tasks(idempotency_key);

CREATE TABLE IF NOT EXISTS execution_steps (
    id BIGSERIAL PRIMARY KEY,
    execution_id TEXT NOT NULL REFERENCES plan_executions(execution_id) ON DELETE CASCADE,
    step_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    command_id TEXT,
    target_node_id TEXT,
    idempotency_key TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    result JSONB DEFAULT '{}'::jsonb,
    UNIQUE (execution_id, step_id)
);

CREATE INDEX IF NOT EXISTS idx_execution_steps_status ON execution_steps(status);

CREATE TABLE IF NOT EXISTS node_locks (
    node_id TEXT PRIMARY KEY,
    lock_id TEXT NOT NULL,
    plan_id TEXT,
    execution_id TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_node_locks_expires ON node_locks(expires_at);

CREATE TABLE IF NOT EXISTS audit_events (
    id BIGSERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor TEXT NOT NULL DEFAULT 'system',
    event_type TEXT NOT NULL,
    plan_id TEXT,
    step_id INTEGER,
    node_id TEXT,
    schema_version TEXT,
    policy_decision TEXT,
    idempotency_key TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    result TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_audit_events_time ON audit_events(time DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_plan ON audit_events(plan_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_audit_events_node ON audit_events(node_id, time DESC);

CREATE TABLE IF NOT EXISTS knowledge_cases (
    id BIGSERIAL PRIMARY KEY,
    case_id TEXT UNIQUE NOT NULL,
    anomaly_pattern TEXT,
    diagnosis_summary TEXT,
    root_cause TEXT,
    resolution TEXT,
    effectiveness DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_pattern ON knowledge_cases USING GIN(to_tsvector('english', anomaly_pattern));
CREATE INDEX IF NOT EXISTS idx_knowledge_effectiveness ON knowledge_cases(effectiveness DESC);

COMMENT ON TABLE raw_logs IS 'Layer 0 raw telemetry logs';
COMMENT ON TABLE anomaly_logs IS 'Layer 1 canonical anomaly output with 60s dedup key';
COMMENT ON TABLE diagnosis_reports IS 'System Agent diagnosis reports and executable FixingPlan v2 JSON';
COMMENT ON TABLE agent_nodes IS 'Registered Ubuntu target nodes and pi-agent capabilities';
COMMENT ON TABLE plan_approvals IS 'Layer 3 approval decisions with expiry';
COMMENT ON TABLE plan_executions IS 'Knowledge Agent FixingPlan execution requests';
COMMENT ON TABLE idempotency_records IS '30-minute idempotency TTL records for plan execution and AgentTask dispatch';
COMMENT ON TABLE audit_events IS 'Append-only execution, approval, and policy audit log';
