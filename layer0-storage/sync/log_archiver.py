"""
Log Archiver Service
從 Loki (tenant: raw) 拉取日誌並持久化到 TimescaleDB
"""
import os
import time
import asyncio
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import asyncpg
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LogArchiver:
    """Log Archiver - Loki → TimescaleDB"""

    def __init__(
        self,
        loki_url: str = "http://loki:3100",
        db_host: str = "timescaledb",
        db_port: int = 5432,
        db_name: str = "logdb",
        db_user: str = "logdb",
        db_password: str = "logdb_password",
        poll_interval: int = 60,
        batch_size: int = 1000
    ):
        self.loki_url = loki_url
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.poll_interval = poll_interval
        self.batch_size = batch_size

        self.pool: Optional[asyncpg.Pool] = None
        self.last_timestamp: Optional[datetime] = None

    async def init_db(self):
        """Initialize database connection pool"""
        logger.info(f"Connecting to TimescaleDB at {self.db_host}:{self.db_port}")
        self.pool = await asyncpg.create_pool(
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            user=self.db_user,
            password=self.db_password,
            min_size=2,
            max_size=10
        )
        logger.info("Database connection pool created")

    async def close_db(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed")

    async def get_last_archived_timestamp(self) -> Optional[datetime]:
        """Get the timestamp of the last archived log"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(time) as last_time FROM raw_logs"
            )
            if row and row['last_time']:
                logger.info(f"Last archived timestamp: {row['last_time']}")
                return row['last_time']
            else:
                # If no logs yet, start from 1 hour ago
                start_time = datetime.utcnow() - timedelta(hours=1)
                logger.info(f"No previous logs found, starting from {start_time}")
                return start_time

    async def query_loki(self, start_time: datetime, end_time: datetime) -> List[Dict]:
        """Query logs from Loki"""
        # Convert to nanoseconds timestamp
        start_ns = int(start_time.timestamp() * 1e9)
        end_ns = int(end_time.timestamp() * 1e9)

        # LogQL query - get all logs from raw tenant
        query = '{job="docker"}'

        url = f"{self.loki_url}/loki/api/v1/query_range"
        params = {
            'query': query,
            'start': start_ns,
            'end': end_ns,
            'limit': self.batch_size
        }

        headers = {
            'X-Scope-OrgID': 'raw'  # Loki tenant ID
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        logger.error(f"Loki query failed: {response.status}")
                        return []

                    data = await response.json()
                    logs = []

                    # Parse Loki response
                    for stream in data.get('data', {}).get('result', []):
                        labels = stream.get('stream', {})
                        for entry in stream.get('values', []):
                            timestamp_ns, message = entry
                            logs.append({
                                'time': datetime.fromtimestamp(int(timestamp_ns) / 1e9),
                                'container': labels.get('container', ''),
                                'service': labels.get('compose_service', ''),
                                'compose_project': labels.get('compose_project', ''),
                                'source': labels.get('source', 'docker'),
                                'message': message,
                                'labels': labels
                            })

                    logger.info(f"Fetched {len(logs)} logs from Loki")
                    return logs

            except Exception as e:
                logger.error(f"Error querying Loki: {e}")
                return []

    async def insert_logs(self, logs: List[Dict]):
        """Insert logs into TimescaleDB"""
        if not logs:
            return

        import json

        async with self.pool.acquire() as conn:
            # Prepare data for batch insert
            await conn.executemany(
                """
                INSERT INTO raw_logs (time, container, service, compose_project, source, message, labels)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (time, container) DO NOTHING
                """,
                [(
                    log['time'],
                    log['container'],
                    log['service'],
                    log['compose_project'],
                    log['source'],
                    log['message'],
                    json.dumps(log['labels'])  # Convert dict to JSON string
                ) for log in logs]
            )

        logger.info(f"Inserted {len(logs)} logs into TimescaleDB")

    async def run_once(self):
        """Run one archiving cycle"""
        # Get last archived timestamp
        if not self.last_timestamp:
            self.last_timestamp = await self.get_last_archived_timestamp()

        # Query logs from last timestamp to now
        end_time = datetime.utcnow()
        logs = await self.query_loki(self.last_timestamp, end_time)

        if logs:
            await self.insert_logs(logs)
            self.last_timestamp = end_time
        else:
            logger.info("No new logs to archive")

    async def run_forever(self):
        """Run archiver continuously"""
        await self.init_db()

        try:
            while True:
                try:
                    await self.run_once()
                except Exception as e:
                    logger.error(f"Error in archiving cycle: {e}", exc_info=True)

                # Wait before next poll
                await asyncio.sleep(self.poll_interval)
        finally:
            await self.close_db()


async def main():
    """Main entry point"""
    archiver = LogArchiver(
        loki_url=os.getenv('LOKI_URL', 'http://loki:3100'),
        db_host=os.getenv('DB_HOST', 'timescaledb'),
        db_port=int(os.getenv('DB_PORT', '5432')),
        db_name=os.getenv('DB_NAME', 'logdb'),
        db_user=os.getenv('DB_USER', 'logdb'),
        db_password=os.getenv('DB_PASSWORD', 'logdb_password'),
        poll_interval=int(os.getenv('POLL_INTERVAL', '60')),
        batch_size=int(os.getenv('BATCH_SIZE', '1000'))
    )

    await archiver.run_forever()


if __name__ == '__main__':
    asyncio.run(main())
