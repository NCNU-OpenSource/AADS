"""
Raw Log Fetcher - Retrieve contextual logs from TimescaleDB

Fetches RAW logs from permanent storage to provide context for anomaly analysis.
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import asyncpg

logger = logging.getLogger(__name__)


class RawLogFetcher:
    """
    Fetch RAW logs from TimescaleDB for deep analysis

    Features:
    - Time-range queries with context window
    - Container/service filtering
    - Efficient pagination
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
        Initialize raw log fetcher

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

    async def connect(self):
        """Initialize database connection pool"""
        if self.pool:
            return

        logger.info(f"Connecting to TimescaleDB at {self.host}:{self.port}")
        self.pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.database,
            user=self.user,
            password=self.password,
            min_size=self.pool_min_size,
            max_size=self.pool_max_size
        )
        logger.info("TimescaleDB connection pool created")

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("TimescaleDB connection pool closed")

    async def fetch_context_logs(
        self,
        timestamp: datetime,
        container: Optional[str] = None,
        service: Optional[str] = None,
        before_minutes: int = 5,
        after_minutes: int = 5,
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Fetch contextual logs around a specific timestamp

        Args:
            timestamp: Center timestamp (anomaly time)
            container: Filter by container name
            service: Filter by service name
            before_minutes: Minutes before timestamp
            after_minutes: Minutes after timestamp
            limit: Maximum number of logs to return

        Returns:
            List of log dictionaries
        """
        if not self.pool:
            await self.connect()

        start_time = timestamp - timedelta(minutes=before_minutes)
        end_time = timestamp + timedelta(minutes=after_minutes)

        query = """
            SELECT time, container, service, compose_project, source, message, labels
            FROM raw_logs
            WHERE time >= $1 AND time <= $2
        """
        params = [start_time, end_time]

        if container:
            params.append(container)
            query += f" AND container = ${len(params)}"

        if service:
            params.append(service)
            query += f" AND service = ${len(params)}"

        query += f" ORDER BY time ASC LIMIT ${len(params) + 1}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        logs = [dict(row) for row in rows]
        logger.info(
            f"Fetched {len(logs)} context logs "
            f"(time range: {start_time} to {end_time})"
        )

        return logs

    async def fetch_logs_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None,
        service: Optional[str] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Fetch logs within a specific time range

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            container: Filter by container name
            service: Filter by service name
            limit: Maximum number of logs to return

        Returns:
            List of log dictionaries
        """
        if not self.pool:
            await self.connect()

        query = """
            SELECT time, container, service, compose_project, source, message, labels
            FROM raw_logs
            WHERE time >= $1 AND time <= $2
        """
        params = [start_time, end_time]

        if container:
            params.append(container)
            query += f" AND container = ${len(params)}"

        if service:
            params.append(service)
            query += f" AND service = ${len(params)}"

        query += f" ORDER BY time ASC LIMIT ${len(params) + 1}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        logs = [dict(row) for row in rows]
        logger.info(f"Fetched {len(logs)} logs from time range query")

        return logs

    async def fetch_logs_by_pattern(
        self,
        pattern: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        container: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch logs matching a specific message pattern

        Args:
            pattern: SQL LIKE pattern (e.g., '%error%', '%timeout%')
            start_time: Optional start timestamp
            end_time: Optional end timestamp
            container: Filter by container name
            limit: Maximum number of logs to return

        Returns:
            List of log dictionaries
        """
        if not self.pool:
            await self.connect()

        query = "SELECT time, container, service, compose_project, source, message, labels FROM raw_logs WHERE message ILIKE $1"
        params = [pattern]

        if start_time:
            params.append(start_time)
            query += f" AND time >= ${len(params)}"

        if end_time:
            params.append(end_time)
            query += f" AND time <= ${len(params)}"

        if container:
            params.append(container)
            query += f" AND container = ${len(params)}"

        query += f" ORDER BY time DESC LIMIT ${len(params) + 1}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

        logs = [dict(row) for row in rows]
        logger.info(f"Fetched {len(logs)} logs matching pattern '{pattern}'")

        return logs

    async def get_log_statistics(
        self,
        start_time: datetime,
        end_time: datetime,
        container: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get log statistics for a time range

        Args:
            start_time: Start timestamp
            end_time: End timestamp
            container: Optional container filter

        Returns:
            Dictionary with statistics
        """
        if not self.pool:
            await self.connect()

        query = """
            SELECT
                COUNT(*) as total_logs,
                COUNT(DISTINCT container) as unique_containers,
                COUNT(DISTINCT service) as unique_services
            FROM raw_logs
            WHERE time >= $1 AND time <= $2
        """
        params = [start_time, end_time]

        if container:
            params.append(container)
            query += f" AND container = ${len(params)}"

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)

        return dict(row) if row else {}
