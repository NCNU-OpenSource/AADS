-- TimescaleDB 資料保留策略
-- 設定資料壓縮和保留政策

-- raw_logs 保留策略
-- 壓縮：7 天以上的資料
-- 保留：永久（不自動刪除，但可以手動歸檔）

-- 啟用自動壓縮
ALTER TABLE raw_logs SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'container',
    timescaledb.compress_orderby = 'time DESC'
);

-- 壓縮 7 天前的資料
SELECT add_compression_policy('raw_logs', INTERVAL '7 days');

-- anomaly_logs 保留策略
-- 保留：永久（重要訓練資料）
-- 無壓縮（需要頻繁查詢和更新）

-- diagnosis_reports 保留策略
-- 保留 1 年
SELECT add_retention_policy('diagnosis_reports', INTERVAL '1 year', if_not_exists => TRUE);

-- 建立統計資訊視圖
CREATE MATERIALIZED VIEW IF NOT EXISTS raw_logs_stats AS
SELECT
    time_bucket('1 hour', time) AS hour,
    container,
    service,
    COUNT(*) AS log_count
FROM raw_logs
GROUP BY hour, container, service;

CREATE INDEX IF NOT EXISTS idx_raw_logs_stats_hour ON raw_logs_stats(hour DESC);

-- 每小時重新整理統計
CREATE OR REPLACE FUNCTION refresh_raw_logs_stats()
RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY raw_logs_stats;
END;
$$ LANGUAGE plpgsql;

-- 建立異常統計視圖
CREATE MATERIALIZED VIEW IF NOT EXISTS anomaly_stats AS
SELECT
    time_bucket('1 hour', time) AS hour,
    container,
    filter_stage,
    COUNT(*) AS anomaly_count,
    AVG(anomaly_score) AS avg_score,
    MAX(anomaly_score) AS max_score
FROM anomaly_logs
GROUP BY hour, container, filter_stage;

CREATE INDEX IF NOT EXISTS idx_anomaly_stats_hour ON anomaly_stats(hour DESC);

COMMENT ON MATERIALIZED VIEW raw_logs_stats IS 'RAW log 每小時統計';
COMMENT ON MATERIALIZED VIEW anomaly_stats IS '異常日誌每小時統計';
