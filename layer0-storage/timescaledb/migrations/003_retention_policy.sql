-- Retention policies. anomaly_logs, workflow, and audit tables intentionally
-- do not use Timescale retention in v1 so incident review data stays available.

SELECT add_retention_policy(
    'raw_logs',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

SELECT hypertable_name
FROM timescaledb_information.hypertables
ORDER BY hypertable_name;
