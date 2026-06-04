-- V2 migration: replace the legacy catalog `supported_commands` column on
-- agent_nodes with `runner_capabilities`. The On-Device Agent no longer exposes
-- a static command whitelist; `/v1/node/facts` returns runner_capabilities
-- (schema_version / modes / supports_as_root / hook_default). See
-- docs/runner-v2-plan.md.

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_nodes' AND column_name = 'supported_commands'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_nodes' AND column_name = 'runner_capabilities'
    ) THEN
        ALTER TABLE agent_nodes RENAME COLUMN supported_commands TO runner_capabilities;
        ALTER TABLE agent_nodes ALTER COLUMN runner_capabilities SET DEFAULT '{}'::jsonb;
        UPDATE agent_nodes SET runner_capabilities = '{}'::jsonb
        WHERE runner_capabilities IS NULL OR jsonb_typeof(runner_capabilities) = 'array';
    ELSIF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'agent_nodes' AND column_name = 'runner_capabilities'
    ) THEN
        ALTER TABLE agent_nodes ADD COLUMN runner_capabilities JSONB DEFAULT '{}'::jsonb;
    END IF;
END $$;
