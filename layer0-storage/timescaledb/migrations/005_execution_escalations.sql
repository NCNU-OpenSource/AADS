-- Drift detection + pause/escalate (ADR-006).
--
-- 1. plan_approvals.plan_sha256: canonical hash of the exact plan content the
--    human approved. The executor refuses to run a plan that hashes
--    differently (TOCTOU protection; the re-approval seam for future dynamic
--    plan adjustment).
-- 2. execution_escalations: open/resolved queue of drift events that paused an
--    execution. The dashboard lists open escalations and resolves them via
--    Resume (resolution='resumed') or Abort (resolution='aborted').

ALTER TABLE plan_approvals ADD COLUMN IF NOT EXISTS plan_sha256 TEXT;

CREATE TABLE IF NOT EXISTS execution_escalations (
    escalation_id TEXT PRIMARY KEY,              -- esc_<uuid>
    execution_id  TEXT NOT NULL,
    plan_id       TEXT NOT NULL,
    node_id       TEXT,
    drift_type    TEXT NOT NULL,                 -- approved_plan_hash_mismatch | policy_violation |
                                                 -- verification_failed | step_retries_exhausted |
                                                 -- final_verification_failed | unrecoverable_step_state
    severity      TEXT NOT NULL DEFAULT 'high',
    details       JSONB NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT NOT NULL DEFAULT 'open',  -- open | resolved
    resolution    TEXT,                          -- resumed | aborted | rediagnosed
    resolved_by   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_escalations_open ON execution_escalations(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_escalations_plan ON execution_escalations(plan_id);
