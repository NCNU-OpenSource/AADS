-- ============================================
-- ADDS TimescaleDB Migration 003
-- 目的：設定自動清理策略（90 天後刪除）
-- 參考：https://docs.timescale.com/use-timescale/latest/data-retention/
-- ============================================

-- ============================================
-- 保留策略說明
-- ============================================
-- 1. 自動刪除 90 天以上的 Chunks
-- 2. 釋放儲存空間，防止硬碟爆滿
-- 3. 保留策略會在背景執行
-- 4. 可根據需求調整 drop_after 參數

-- ============================================
-- 1. raw_logs 保留策略（90 天）
-- ============================================

SELECT add_retention_policy(
    'raw_logs',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ============================================
-- 2. anomaly_logs 保留策略（90 天）
-- ============================================

SELECT add_retention_policy(
    'anomaly_logs',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ============================================
-- 3. diagnosis_reports 保留策略（90 天）
-- ============================================

SELECT add_retention_policy(
    'diagnosis_reports',
    drop_after => INTERVAL '90 days',
    if_not_exists => TRUE
);

-- ============================================
-- 4. knowledge_cases 保留策略（1 年）
-- 知識庫案例保留較久，因為它們是學習材料
-- ============================================

SELECT add_retention_policy(
    'knowledge_cases',
    drop_after => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- ============================================
-- 5. 檢視保留策略
-- ============================================

SELECT * FROM timescaledb_information.jobs
WHERE proc_name = 'policy_retention';

-- 檢視各表的資料保留情況
SELECT
    hypertable_name,
    COUNT(*) AS total_chunks,
    MIN(range_start) AS oldest_chunk,
    MAX(range_end) AS newest_chunk,
    pg_size_pretty(SUM(total_bytes)) AS total_size
FROM timescaledb_information.chunks
GROUP BY hypertable_name
ORDER BY hypertable_name;

-- ============================================
-- 註解說明：
-- ============================================
-- 1. drop_after 決定保留時間
--    - raw_logs: 90 天（原始日誌）
--    - anomaly_logs: 90 天（異常日誌）
--    - diagnosis_reports: 90 天（診斷報告）
--    - knowledge_cases: 1 年（知識庫案例）
--
-- 2. 保留策略會在背景執行（by TimescaleDB background worker）
--    - 不需要手動觸發
--    - 每次檢查間隔約為 12 小時
--
-- 3. 刪除是以 Chunk 為單位
--    - 不會刪除部分資料
--    - 確保資料一致性
--
-- 4. 修改保留策略：
--    SELECT remove_retention_policy('raw_logs');
--    SELECT add_retention_policy('raw_logs', drop_after => INTERVAL '180 days');
--
-- 5. 手動觸發清理（測試用）：
--    SELECT drop_chunks('raw_logs', older_than => INTERVAL '90 days');
--
-- 6. 檢查即將被刪除的 Chunks：
--    SELECT show_chunks('raw_logs', older_than => INTERVAL '90 days');
--
-- 7. 執行方式：
--    psql -U postgres -d adds -f 003_retention_policy.sql
