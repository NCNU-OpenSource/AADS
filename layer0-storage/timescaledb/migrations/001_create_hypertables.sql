-- Compatibility migration: keep hypertables and indexes aligned with the
-- canonical time column. Older versions referenced a non-existent timestamp
-- column, which broke fresh initialization.
--
-- anomaly_logs intentionally remains a relational table in v1 so
-- UNIQUE(dedup_key) can support Layer 1 first-write-wins deduplication.

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

SELECT create_hypertable('raw_logs', 'time', chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_raw_logs_time ON raw_logs(time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_node_time ON raw_logs(node_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_service_time ON raw_logs(service, time DESC);

CREATE INDEX IF NOT EXISTS idx_anomaly_logs_time ON anomaly_logs(time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_node_time ON anomaly_logs(node_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_service_time ON anomaly_logs(service, time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_score ON anomaly_logs(anomaly_score DESC);

SELECT hypertable_name FROM timescaledb_information.hypertables ORDER BY hypertable_name;
