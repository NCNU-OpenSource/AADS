"""
Anomaly Consumer - Consume anomaly logs from PostgreSQL

Polls the anomaly_logs table and feeds anomalies to the analysis pipeline.
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, timedelta
import asyncpg

logger = logging.getLogger(__name__)


class AnomalyConsumer:
    """
    Consume anomaly logs from PostgreSQL for analysis

    Features:
    - Periodic polling of anomaly_logs table
    - Batch processing
    - Offset tracking (only process new anomalies)
    - Callback-based architecture
    """

    def __init__(
        self,
        host: str = "timescaledb",
        port: int = 5432,
        database: str = "logdb",
        user: str = "logdb",
        password: str = "logdb_password",
        poll_interval: int = 30,
        batch_size: int = 50
    ):
        """
        Initialize anomaly consumer

        Args:
            host: PostgreSQL host
            port: PostgreSQL port
            database: Database name
            user: Database user
            password: Database password
            poll_interval: Seconds between polls
            batch_size: Number of anomalies per batch
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        self.pool: Optional[asyncpg.Pool] = None
        self.last_processed_id: int = 0
        self.callback: Optional[Callable] = None

        self.stats = {
            "total_consumed": 0,
            "total_batches": 0,
            "last_poll_time": None
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
            min_size=2,
            max_size=10
        )
        logger.info("Database connection pool created")

        # Get the last processed ID
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COALESCE(MAX(id), 0) as max_id FROM anomaly_logs")
            self.last_processed_id = row['max_id']
            logger.info(f"Starting from anomaly ID: {self.last_processed_id}")

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Database connection pool closed")

    def set_callback(self, callback: Callable[[List[Dict[str, Any]]], None]):
        """
        Set callback function to process anomaly batches

        Args:
            callback: Async function that takes a list of anomaly dictionaries
        """
        self.callback = callback

    async def poll_once(self) -> int:
        """
        Poll for new anomalies once

        Returns:
            Number of anomalies consumed
        """
        if not self.pool:
            await self.connect()

        # Query new anomalies
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, time, container, service, compose_project,
                       raw_message, template, anomaly_score, filter_stage, labels
                FROM anomaly_logs
                WHERE id > $1
                ORDER BY id ASC
                LIMIT $2
                """,
                self.last_processed_id,
                self.batch_size
            )

        if not rows:
            logger.debug("No new anomalies found")
            return 0

        anomalies = [dict(row) for row in rows]
        count = len(anomalies)

        logger.info(f"Consumed {count} new anomalies (IDs: {anomalies[0]['id']} - {anomalies[-1]['id']})")

        # Update last processed ID
        self.last_processed_id = anomalies[-1]['id']

        # Call callback if set
        if self.callback:
            try:
                await self.callback(anomalies)
            except Exception as e:
                logger.error(f"Error in callback: {e}", exc_info=True)

        # Update statistics
        self.stats["total_consumed"] += count
        self.stats["total_batches"] += 1
        self.stats["last_poll_time"] = datetime.now()

        return count

    async def run_forever(self):
        """
        Run consumer continuously

        Polls for new anomalies every poll_interval seconds
        """
        await self.connect()

        logger.info(f"Starting anomaly consumer (poll interval: {self.poll_interval}s)")

        try:
            while True:
                try:
                    await self.poll_once()
                except Exception as e:
                    logger.error(f"Error during polling: {e}", exc_info=True)

                # Wait before next poll
                await asyncio.sleep(self.poll_interval)

        except KeyboardInterrupt:
            logger.info("Consumer stopped by user")
        finally:
            await self.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get consumer statistics"""
        return dict(self.stats)
