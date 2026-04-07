-- TimescaleDB 初始化腳本
-- 用於存儲 RAW Log 永久資料

-- 啟用 TimescaleDB 擴展
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- 建立 raw_logs 表
CREATE TABLE IF NOT EXISTS raw_logs (
    time TIMESTAMPTZ NOT NULL,
    container TEXT,
    service TEXT,
    compose_project TEXT,
    source TEXT,
    message TEXT,
    labels JSONB,
    PRIMARY KEY (time, container)
);

-- 轉換為 hypertable（時序表）
SELECT create_hypertable('raw_logs', 'time', if_not_exists => TRUE);

-- 建立索引
CREATE INDEX IF NOT EXISTS idx_raw_logs_container ON raw_logs(container, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_service ON raw_logs(service, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_project ON raw_logs(compose_project, time DESC);
CREATE INDEX IF NOT EXISTS idx_raw_logs_source ON raw_logs(source);

-- GIN 索引用於 JSONB 查詢
CREATE INDEX IF NOT EXISTS idx_raw_logs_labels ON raw_logs USING GIN(labels);

-- 建立 anomaly_logs 表（存儲異常日誌）
CREATE TABLE IF NOT EXISTS anomaly_logs (
    id SERIAL PRIMARY KEY,
    time TIMESTAMPTZ NOT NULL,
    container TEXT,
    service TEXT,
    compose_project TEXT,
    raw_message TEXT,
    template TEXT,              -- Drain3 提取的模板
    anomaly_score FLOAT,
    filter_stage TEXT,          -- 'rf', 'logbert', 'transformer'
    is_confirmed BOOLEAN DEFAULT NULL,  -- 人工確認（用於重訓練）
    labels JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 建立索引
CREATE INDEX IF NOT EXISTS idx_anomaly_time ON anomaly_logs(time DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_container ON anomaly_logs(container);
CREATE INDEX IF NOT EXISTS idx_anomaly_score ON anomaly_logs(anomaly_score DESC);
CREATE INDEX IF NOT EXISTS idx_anomaly_confirmed ON anomaly_logs(is_confirmed);
CREATE INDEX IF NOT EXISTS idx_anomaly_filter_stage ON anomaly_logs(filter_stage);

-- 建立診斷報告表（Layer 2 輸出）
CREATE TABLE IF NOT EXISTS diagnosis_reports (
    id SERIAL PRIMARY KEY,
    diagnosis_id TEXT UNIQUE NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    severity TEXT,              -- 'low', 'medium', 'high', 'critical'
    summary TEXT,
    root_cause JSONB,           -- 根因分析結果
    affected_services JSONB,    -- 受影響的服務列表
    correlated_metrics JSONB,   -- 關聯的指標
    recommended_actions JSONB,  -- 修復建議
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_diagnosis_timestamp ON diagnosis_reports(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_diagnosis_severity ON diagnosis_reports(severity);

-- 建立知識庫案例表（歷史案例存儲）
CREATE TABLE IF NOT EXISTS knowledge_cases (
    id SERIAL PRIMARY KEY,
    case_id TEXT UNIQUE NOT NULL,
    anomaly_pattern TEXT,       -- 異常模式描述
    diagnosis_summary TEXT,
    root_cause TEXT,
    resolution TEXT,            -- 解決方案
    effectiveness FLOAT,        -- 效果評分 (0-1)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_pattern ON knowledge_cases USING GIN(to_tsvector('english', anomaly_pattern));
CREATE INDEX IF NOT EXISTS idx_knowledge_effectiveness ON knowledge_cases(effectiveness DESC);

COMMENT ON TABLE raw_logs IS 'RAW log 永久存儲，來自 Loki raw tenant';
COMMENT ON TABLE anomaly_logs IS 'Layer 1 檢測到的異常日誌，永久保存供重訓練';
COMMENT ON TABLE diagnosis_reports IS 'Layer 2 根因分析診斷報告';
COMMENT ON TABLE knowledge_cases IS 'Layer 2 知識庫歷史案例';
