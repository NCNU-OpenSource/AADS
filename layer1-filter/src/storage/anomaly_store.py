"""
Anomaly Store - Persist anomaly logs to PostgreSQL/TimescaleDB

Writes anomaly logs to the anomaly_logs table for:
- Long-term storage and analysis
- Model retraining
- Manual review and labeling
"""
import asyncio
import json
import logging
import hashlib
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import asyncpg

from filters.base import FilterResult

logger = logging.getLogger(__name__)


class AnomalyStore:
    """
    Store anomaly logs in PostgreSQL/TimescaleDB

    Features:
    - Batch inserts for performance
    - Async operations for non-blocking writes
    - Deduplication (same log+timestamp won't be inserted twice)
    - Connection pooling
    """

    def __init__(
        self,
        host: str = "timescaledb",
        port: int = 5432,
        database: str = "logdb",
        user: str = "logdb",
        password: str = "logdb_password",
        pool_min_size: int = 2,
        pool_max_size: int = 10
    ):
        """
        Initialize anomaly store

        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            database: Database name
            user: Database user
            password: Database password
            pool_min_size: Minimum connection pool size
            pool_max_size: Maximum connection pool size
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size

        self.pool: Optional[asyncpg.Pool] = None
        self.stats = {
            "total_written": 0,
            "total_duplicates": 0,
            "total_errors": 0
        }

    async def connect(self):
        """Initialize database connection pool"""
        if self.pool:
            return

        logger.info(f"Connecting to PostgreSQL at {self.host}:{self.port}")
        self.pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size
        )
        logger.info("Database connection pool created")

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Database connection pool closed")

    async def store_anomalies(self, results: List[FilterResult]) -> int:
        """
        Store anomaly logs in database

        Args:
            results: List of FilterResult objects

        Returns:
            Number of anomalies successfully written
        """
        if not self.pool:
            await self.connect()

        # Filter only anomalies
        anomalies = [r for r in results if r.is_anomaly]

        if not anomalies:
            return 0

        written_count = 0
        duplicate_count = 0
        error_count = 0

        async with self.pool.acquire() as conn:
            for result in anomalies:
                try:
                    log = result.log
                    timestamp = log.get('time') or log.get('timestamp')

                    # Parse timestamp if it's a string
                    if isinstance(timestamp, str):
                        # Try parsing nanoseconds timestamp
                        try:
                            timestamp = datetime.fromtimestamp(int(timestamp) / 1e9, tz=timezone.utc)
                        except (ValueError, OverflowError):
                            timestamp = datetime.now(timezone.utc)
                    elif timestamp is None:
                        timestamp = datetime.now(timezone.utc)

                    # Extract fields
                    container = log.get('labels', {}).get('container', log.get('container', ''))
                    service = log.get('labels', {}).get('compose_service', log.get('service', ''))
                    compose_project = log.get('labels', {}).get('compose_project', log.get('compose_project', ''))
                    node_id = log.get('node_id') or log.get('labels', {}).get('node_id', 'controller')
                    raw_message = log.get('message', '')
                    template = log.get('template') or self._template_from_message(raw_message)
                    dedup_key = self._dedup_key(node_id, service or container, template, timestamp)

                    # Insert into database
                    status = await conn.execute(
                        """
                        INSERT INTO anomaly_logs
                        (time, node_id, container, service, compose_project, raw_message, template,
                         anomaly_score, filter_stage, filter_metadata, labels, dedup_key,
                         first_seen, last_seen)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $1, $1)
                        ON CONFLICT (dedup_key) DO UPDATE SET
                            occurrence_count = anomaly_logs.occurrence_count + 1,
                            last_seen = GREATEST(anomaly_logs.last_seen, EXCLUDED.last_seen),
                            anomaly_score = GREATEST(anomaly_logs.anomaly_score, EXCLUDED.anomaly_score),
                            filter_stage = CASE
                                WHEN anomaly_logs.filter_stage = EXCLUDED.filter_stage THEN anomaly_logs.filter_stage
                                ELSE anomaly_logs.filter_stage || '+' || EXCLUDED.filter_stage
                            END,
                            filter_metadata = anomaly_logs.filter_metadata || EXCLUDED.filter_metadata,
                            labels = anomaly_logs.labels || EXCLUDED.labels
                        """,
                        timestamp,
                        node_id,
                        container,
                        service,
                        compose_project,
                        raw_message,
                        template,
                        result.anomaly_score,
                        result.filter_stage,
                        json.dumps(result.metadata or {}),
                        json.dumps(log.get('labels', {})),
                        dedup_key,
                    )

                    if status == "INSERT 0 1":
                        written_count += 1
                    else:
                        duplicate_count += 1

                except asyncpg.UniqueViolationError:
                    duplicate_count += 1
                except Exception as e:
                    error_count += 1
                    logger.error(f"Error storing anomaly: {e}")

        # Update statistics
        self.stats["total_written"] += written_count
        self.stats["total_duplicates"] += duplicate_count
        self.stats["total_errors"] += error_count

        logger.info(
            f"Stored {written_count} anomalies "
            f"(duplicates: {duplicate_count}, errors: {error_count})"
        )

        return written_count

    def _template_from_message(self, message: str) -> str:
        """Create a stable coarse template without introducing a parser dependency."""
        import re

        normalized = re.sub(r"\b\d+\b", "<num>", message or "")
        normalized = re.sub(r"0x[0-9a-fA-F]+", "<hex>", normalized)
        return normalized[:200]

    def _dedup_key(self, node_id: str, service: str, template: str, timestamp: datetime) -> str:
        minute_bucket = timestamp.replace(second=0, microsecond=0).isoformat()
        raw_key = f"{node_id}|{service}|{template}|{minute_bucket}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    async def store_batch(self, results: List[FilterResult]) -> int:
        """Alias for store_anomalies for compatibility"""
        return await self.store_anomalies(results)

    async def get_last_timestamp(self) -> Optional[datetime]:
        """
        Get timestamp of the last stored anomaly

        Returns:
            Datetime of last anomaly, or None if no anomalies exist
        """
        if not self.pool:
            await self.connect()

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(time) as last_time FROM anomaly_logs"
            )
            return row['last_time'] if row and row['last_time'] else None

    async def get_recent_anomalies(
        self,
        limit: int = 100,
        container: Optional[str] = None,
        filter_stage: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve recent anomalies from database

        Args:
            limit: Maximum number of results
            container: Filter by container name
            filter_stage: Filter by filter stage (e.g., 'logbert')

        Returns:
            List of anomaly log dictionaries
        """
        if not self.pool:
            await self.connect()

        query = "SELECT * FROM anomaly_logs WHERE 1=1"
        params = []

        if container:
            params.append(container)
            query += f" AND container = ${len(params)}"

        if filter_stage:
            params.append(filter_stage)
            query += f" AND filter_stage = ${len(params)}"

        query += f" ORDER BY time DESC LIMIT ${len(params) + 1}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        return [dict(row) for row in rows]

    async def get_unconfirmed_anomalies(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get anomalies that haven't been manually confirmed

        Used for active learning and model retraining

        Args:
            limit: Maximum number of results

        Returns:
            List of unconfirmed anomaly dictionaries
        """
        if not self.pool:
            await self.connect()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM anomaly_logs
                WHERE is_confirmed IS NULL
                ORDER BY time DESC
                LIMIT $1
                """,
                limit
            )

        return [dict(row) for row in rows]

    async def confirm_anomaly(self, anomaly_id: int, is_confirmed: bool) -> bool:
        """
        Manually confirm or reject an anomaly

        Args:
            anomaly_id: ID of anomaly log
            is_confirmed: True if confirmed as anomaly, False if false positive

        Returns:
            True if updated successfully
        """
        if not self.pool:
            await self.connect()

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE anomaly_logs
                SET is_confirmed = $1
                WHERE id = $2
                """,
                is_confirmed,
                anomaly_id
            )

        return result == "UPDATE 1"

    def get_stats(self) -> Dict[str, Any]:
        """Get storage statistics"""
        return dict(self.stats)
