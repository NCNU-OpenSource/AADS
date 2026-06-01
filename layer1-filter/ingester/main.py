"""
ADDS FastAPI Ingester Service
接收 Grafana Alloy 發送的異常資料，寫入 PostgreSQL

架構角色：
- Fan-out Sink A (DB 留存)
- 接收 Alloy HTTP POST
- 寫入 PostgreSQL anomaly_logs 表
"""

import hashlib
import os
import re
from datetime import datetime
from typing import List, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

# ============================================
# FastAPI App Configuration
# ============================================

app = FastAPI(
    title="ADDS Ingester API",
    description="Receive anomaly logs from Grafana Alloy and write to PostgreSQL",
    version="1.0.0",
)

# ============================================
# Database Configuration
# ============================================

DB_HOST = os.getenv("DB_HOST", "timescaledb")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_NAME = os.getenv("DB_NAME", "adds")

# Connection pool
db_pool: Optional[asyncpg.Pool] = None


# ============================================
# Data Models
# ============================================


class AnomalyLog(BaseModel):
    """
    Anomaly log data model
    接收自 Alloy 的異常日誌資料
    """

    timestamp: datetime = Field(..., description="Log timestamp")
    node_id: str = Field(default="controller", description="AADS node id")
    service: str = Field(..., description="Service name")
    container: str = Field(default="", description="Container name")
    log_message: str = Field(..., description="Raw log message")
    template: str = Field(default="", description="Normalized log template")
    anomaly_score: float = Field(default=1.0, description="Canonical anomaly score")
    logbert_anomaly_score: Optional[float] = Field(default=None, description="Legacy LogBERT anomaly score")
    filter_stage: str = Field(default="ingester", description="Filter stage")
    is_anomaly: bool = Field(default=True, description="Is anomaly flag")


class AnomalyBatch(BaseModel):
    """
    Batch of anomaly logs
    支援批次寫入以提升效能
    """

    logs: List[AnomalyLog] = Field(..., description="List of anomaly logs")


# ============================================
# Database Connection Management
# ============================================


@app.on_event("startup")
async def startup():
    """Initialize database connection pool on startup"""
    global db_pool

    try:
        db_pool = await asyncpg.create_pool(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            min_size=5,
            max_size=20,
            command_timeout=60,
        )
        print(f"✅ Database connection pool created: {DB_HOST}:{DB_PORT}/{DB_NAME}")
    except Exception as e:
        print(f"❌ Failed to create database pool: {e}")
        raise


@app.on_event("shutdown")
async def shutdown():
    """Close database connection pool on shutdown"""
    global db_pool

    if db_pool:
        await db_pool.close()
        print("✅ Database connection pool closed")


# ============================================
# API Endpoints
# ============================================


@app.get("/health")
async def health_check():
    """
    Health check endpoint
    檢查服務與資料庫連線狀態
    """
    if db_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not initialized",
        )

    try:
        async with db_pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            if result == 1:
                return {
                    "status": "healthy",
                    "database": "connected",
                    "timestamp": datetime.utcnow().isoformat(),
                }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connection failed: {str(e)}",
        )


@app.post("/api/anomalies", status_code=status.HTTP_201_CREATED)
async def create_anomaly(log: AnomalyLog):
    """
    Create a single anomaly log entry
    接收單筆異常日誌並寫入資料庫
    """
    if db_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not initialized",
        )

    try:
        async with db_pool.acquire() as conn:
            template = log.template or _template_from_message(log.log_message)
            score = log.logbert_anomaly_score if log.logbert_anomaly_score is not None else log.anomaly_score
            dedup_key = _dedup_key(log.node_id, log.service or log.container, template, log.timestamp)
            query = """
                INSERT INTO anomaly_logs (
                    time, node_id, container, service, raw_message, template,
                    anomaly_score, filter_stage, dedup_key, first_seen, last_seen
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $1, $1)
                ON CONFLICT (dedup_key) DO UPDATE SET
                    occurrence_count = anomaly_logs.occurrence_count + 1,
                    last_seen = GREATEST(anomaly_logs.last_seen, EXCLUDED.last_seen),
                    anomaly_score = GREATEST(anomaly_logs.anomaly_score, EXCLUDED.anomaly_score),
                    filter_stage = anomaly_logs.filter_stage || '+' || EXCLUDED.filter_stage
                RETURNING id
            """

            log_id = await conn.fetchval(
                query,
                log.timestamp,
                log.node_id,
                log.container,
                log.service,
                log.log_message,
                template,
                score,
                log.filter_stage,
                dedup_key,
            )

            return {
                "id": log_id,
                "message": "Anomaly log created successfully",
                "timestamp": log.timestamp.isoformat(),
            }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to insert anomaly log: {str(e)}",
        )


@app.post("/api/anomalies/batch", status_code=status.HTTP_201_CREATED)
async def create_anomaly_batch(batch: AnomalyBatch):
    """
    Create multiple anomaly log entries in batch
    批次寫入異常日誌，提升寫入效能
    """
    if db_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database pool not initialized",
        )

    if not batch.logs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch cannot be empty",
        )

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                query = """
                    INSERT INTO anomaly_logs (
                        time, node_id, container, service, raw_message, template,
                        anomaly_score, filter_stage, dedup_key, first_seen, last_seen
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $1, $1)
                    ON CONFLICT (dedup_key) DO UPDATE SET
                        occurrence_count = anomaly_logs.occurrence_count + 1,
                        last_seen = GREATEST(anomaly_logs.last_seen, EXCLUDED.last_seen),
                        anomaly_score = GREATEST(anomaly_logs.anomaly_score, EXCLUDED.anomaly_score),
                        filter_stage = anomaly_logs.filter_stage || '+' || EXCLUDED.filter_stage
                """

                # Prepare batch data
                batch_data = [
                    (
                        log.timestamp,
                        log.node_id,
                        log.container,
                        log.service,
                        log.log_message,
                        log.template or _template_from_message(log.log_message),
                        log.logbert_anomaly_score if log.logbert_anomaly_score is not None else log.anomaly_score,
                        log.filter_stage,
                        _dedup_key(
                            log.node_id,
                            log.service or log.container,
                            log.template or _template_from_message(log.log_message),
                            log.timestamp,
                        ),
                    )
                    for log in batch.logs
                ]

                # Execute batch insert
                await conn.executemany(query, batch_data)

                return {
                    "message": f"Batch of {len(batch.logs)} anomaly logs created successfully",
                    "count": len(batch.logs),
                }

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to insert batch anomaly logs: {str(e)}",
        )


def _template_from_message(message: str) -> str:
    normalized = re.sub(r"\b\d+\b", "<num>", message or "")
    normalized = re.sub(r"0x[0-9a-fA-F]+", "<hex>", normalized)
    return normalized[:200]


def _dedup_key(node_id: str, service: str, template: str, timestamp: datetime) -> str:
    minute_bucket = timestamp.replace(second=0, microsecond=0).isoformat()
    raw_key = f"{node_id}|{service}|{template}|{minute_bucket}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


# ============================================
# Main Entry Point
# ============================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
