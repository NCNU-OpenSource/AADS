-- ============================================
-- ADDS TimescaleDB Migration 001
-- 目的：建立 Hypertables（時間分區表）
-- 參考：https://docs.timescale.com/use-timescale/latest/hypertables/
-- ============================================

-- ============================================
-- 1. 檢查 TimescaleDB 擴展
-- ============================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================
-- 2. 建立 raw_logs Hypertable
-- ============================================

-- 如果表格已存在，先轉換為 Hypertable
SELECT create_hypertable(
    'raw_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 建立索引以提升查詢效能
CREATE INDEX IF NOT EXISTS idx_raw_logs_timestamp ON raw_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_service ON raw_logs (service, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_level ON raw_logs (level, timestamp DESC);

-- ============================================
-- 3. 建立 anomaly_logs Hypertable
-- ============================================

SELECT create_hypertable(
    'anomaly_logs',
    'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 建立索引
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_timestamp ON anomaly_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_service ON anomaly_logs (service, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_logs_logbert_score ON anomaly_logs (logbert_anomaly_score DESC);

-- ============================================
-- 4. 建立 diagnosis_reports Hypertable
-- ============================================

SELECT create_hypertable(
    'diagnosis_reports',
    'created_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 建立索引
CREATE INDEX IF NOT EXISTS idx_diagnosis_reports_created_at ON diagnosis_reports (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_diagnosis_reports_layer ON diagnosis_reports (layer, created_at DESC);

-- ============================================
-- 5. 建立 knowledge_cases Hypertable
-- ============================================

SELECT create_hypertable(
    'knowledge_cases',
    'created_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- 建立索引
CREATE INDEX IF NOT EXISTS idx_knowledge_cases_created_at ON knowledge_cases (created_at DESC);

-- ============================================
-- 6. 顯示 Hypertables 資訊
-- ============================================

SELECT * FROM timescaledb_information.hypertables;

-- ============================================
-- 註解說明：
-- ============================================
-- 1. chunk_time_interval 決定分區大小
--    - raw_logs / anomaly_logs: 1 天（高頻寫入）
--    - diagnosis_reports: 1 天（中頻寫入）
--    - knowledge_cases: 7 天（低頻寫入）
--
-- 2. 索引策略：
--    - 時間戳降序索引（最新資料優先）
--    - 複合索引（service + timestamp）加速過濾查詢
--
-- 3. 執行方式：
--    psql -U postgres -d adds -f 001_create_hypertables.sql
