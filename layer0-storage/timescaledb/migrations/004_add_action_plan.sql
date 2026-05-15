-- Migration 004: Add action_plan column to diagnosis_reports
-- Bug fix: layer2-analyzer INSERT includes action_plan but table was missing this column,
-- causing every store_diagnosis() call to fail with UndefinedColumnError.

ALTER TABLE diagnosis_reports
    ADD COLUMN IF NOT EXISTS action_plan JSONB;

COMMENT ON COLUMN diagnosis_reports.action_plan IS 'ClaudeStylePlan from LangGraph agent analysis';
