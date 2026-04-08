-- ============================================
-- ADDS TimescaleDB Migration 002
-- 目的：設定自動壓縮策略（7 天後轉為列式儲存）
-- 參考：https://docs.timescale.com/use-timescale/latest/compression/
-- ============================================

-- ============================================
-- 壓縮策略說明
-- ============================================
-- 1. 壓縮可節省 90%+ 的儲存空間
-- 2. 7 天是熱/冷資料的分界點
-- 3. LogBERT 主要讀取 7 天內的資料（熱資料）
-- 4. 壓縮後的資料讀取較慢，但空間節省顯著

-- ============================================
-- 1. 啟用 raw_logs 壓縮
-- ============================================

-- 設定壓縮欄位（order by timestamp, segment by service）
ALTER TABLE raw_logs SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'service'
);

-- 添加壓縮策略：7 天後自動壓縮
SELECT add_compression_policy(
    'raw_logs',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================
-- 2. 啟用 anomaly_logs 壓縮
-- ============================================

ALTER TABLE anomaly_logs SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'timestamp DESC',
    timescaledb.compress_segmentby = 'service'
);

SELECT add_compression_policy(
    'anomaly_logs',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================
-- 3. 啟用 diagnosis_reports 壓縮
-- ============================================

ALTER TABLE diagnosis_reports SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'created_at DESC',
    timescaledb.compress_segmentby = 'layer'
);

SELECT add_compression_policy(
    'diagnosis_reports',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================
-- 4. 啟用 knowledge_cases 壓縮
-- ============================================

ALTER TABLE knowledge_cases SET (
    timescaledb.compress,
    timescaledb.compress_orderby = 'created_at DESC'
);

SELECT add_compression_policy(
    'knowledge_cases',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================
-- 5. 檢視壓縮策略
-- ============================================

SELECT * FROM timescaledb_information.compression_settings;

-- 檢視壓縮統計
SELECT
    hypertable_name,
    total_chunks,
    number_compressed_chunks,
    before_compression_total_bytes,
    after_compression_total_bytes,
    pg_size_pretty(before_compression_total_bytes) AS before_size,
    pg_size_pretty(after_compression_total_bytes) AS after_size,
    ROUND(
        (1 - after_compression_total_bytes::numeric / before_compression_total_bytes::numeric) * 100,
        2
    ) AS compression_ratio_percent
FROM timescaledb_information.hypertable_compression_stats
ORDER BY hypertable_name;

-- ============================================
-- 註解說明：
-- ============================================
-- 1. compress_orderby 決定壓縮後的資料排序
--    - 使用 timestamp DESC 讓最新資料在前
--    - 提升範圍查詢效能
--
-- 2. compress_segmentby 決定壓縮的分段方式
--    - 使用 service 讓同一服務的日誌壓縮在一起
--    - 提升按服務過濾的查詢效能
--
-- 3. 壓縮策略會在背景執行（by TimescaleDB background worker）
--    - 不需要手動觸發
--    - 每次檢查間隔約為 12 小時
--
-- 4. 手動觸發壓縮（測試用）：
--    SELECT compress_chunk(i)
--    FROM show_chunks('raw_logs', older_than => INTERVAL '7 days') i;
--
-- 5. 執行方式：
--    psql -U postgres -d adds -f 002_compression_policy.sql
