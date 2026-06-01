-- Compression policies for hot/cold telemetry tables. Only hypertables are
-- compressed; relational workflow tables and anomaly_logs stay uncompressed
-- so Layer 1 can use UNIQUE(dedup_key) safely.

ALTER TABLE raw_logs SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'time DESC',
    timescaledb.compress_segmentby = 'node_id,service'
);

SELECT add_compression_policy(
    'raw_logs',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT hypertable_name FROM timescaledb_information.compression_settings ORDER BY hypertable_name;
