"""
Layer 1 Filter - Main Entry Point

Multi-layer anomaly detection pipeline:
1. Poll logs from Loki
2. Run through filter pipeline (LogBERT)
3. Store anomalies to PostgreSQL
"""
import asyncio
import logging
import os
from datetime import datetime

from filters.logbert_filter import LogBERTFilter
from storage.anomaly_store import AnomalyStore

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
LOKI_URL = os.getenv('LOKI_URL', 'http://loki:3100')
LOKI_TENANT = os.getenv('LOKI_TENANT', 'raw')
LOKI_QUERY = os.getenv('LOKI_QUERY', '{source="docker", container!="logbert"}')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '10'))

DB_HOST = os.getenv('DB_HOST', 'timescaledb')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'logdb')
DB_USER = os.getenv('DB_USER', 'logdb')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'logdb_password')

ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', '0.5'))
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '50'))


class Layer1FilterService:
    """Main service for Layer 1 anomaly filtering"""

    def __init__(self):
        # Initialize LogBERT filter
        self.filter = LogBERTFilter(
            model_path='bert-base-uncased',
            threshold=ANOMALY_THRESHOLD,
            device='cpu'  # Use GPU if available
        )

        # Initialize anomaly store
        self.store = AnomalyStore(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )

        self.last_timestamp = None
        self.stats = {
            'total_processed': 0,
            'total_anomalies': 0,
            'last_batch': datetime.now()
        }

    async def initialize(self):
        """Initialize components"""
        logger.info("Initializing Layer 1 Filter Service...")

        # Load LogBERT model
        logger.info("Loading LogBERT model...")
        # Model loading is done in LogBERTFilter.__init__

        # Connect to database
        await self.store.connect()
        logger.info("Connected to anomaly store")

        # Get last processed timestamp
        self.last_timestamp = await self.store.get_last_timestamp()
        if self.last_timestamp:
            logger.info(f"Resuming from timestamp: {self.last_timestamp}")
        else:
            logger.info("Starting fresh (no previous anomalies)")

    async def fetch_logs_from_loki(self, start_time, end_time):
        """Fetch logs from Loki"""
        import aiohttp

        params = {
            'query': LOKI_QUERY,
            'start': int(start_time.timestamp() * 1e9),
            'end': int(end_time.timestamp() * 1e9),
            'limit': BATCH_SIZE,
            'direction': 'forward'
        }

        headers = {'X-Scope-OrgID': LOKI_TENANT}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{LOKI_URL}/loki/api/v1/query_range",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    response.raise_for_status()
                    data = await response.json()

                    logs = []
                    if data['status'] == 'success':
                        for stream in data['data']['result']:
                            labels = stream['stream']
                            for value in stream['values']:
                                timestamp_ns, message = value[0], value[1]

                                # Convert timestamp to datetime
                                timestamp = datetime.fromtimestamp(int(timestamp_ns) / 1e9)

                                logs.append({
                                    'timestamp': timestamp,
                                    'labels': labels,
                                    'message': message,
                                    'container': labels.get('container', ''),
                                    'service': labels.get('compose_service', '')
                                })

                    return logs

        except Exception as e:
            logger.error(f"Error fetching logs from Loki: {e}")
            return []

    async def process_batch(self):
        """Process one batch of logs"""
        from datetime import timedelta

        # Calculate time range
        end_time = datetime.utcnow()
        if self.last_timestamp:
            start_time = self.last_timestamp
        else:
            start_time = end_time - timedelta(seconds=30)

        # Fetch logs
        logs = await self.fetch_logs_from_loki(start_time, end_time)

        if not logs:
            logger.debug("No new logs to process")
            return

        logger.info(f"Processing {len(logs)} logs...")

        # Run through LogBERT filter
        results = self.filter.predict(logs)

        # Filter anomalies
        anomalies = [r for r in results if r.is_anomaly]

        if anomalies:
            logger.info(f"Detected {len(anomalies)} anomalies (threshold: {ANOMALY_THRESHOLD})")

            # Store to PostgreSQL
            await self.store.store_batch(anomalies)

            self.stats['total_anomalies'] += len(anomalies)

        self.stats['total_processed'] += len(logs)
        self.last_timestamp = end_time

        # Print stats every 100 logs
        if self.stats['total_processed'] % 100 == 0:
            logger.info(f"Stats: Processed={self.stats['total_processed']}, "
                       f"Anomalies={self.stats['total_anomalies']}, "
                       f"Rate={self.stats['total_anomalies']/self.stats['total_processed']*100:.2f}%")

    async def run(self):
        """Main service loop"""
        await self.initialize()

        logger.info(f"Starting Layer 1 Filter Service")
        logger.info(f"  Loki URL: {LOKI_URL}")
        logger.info(f"  Query: {LOKI_QUERY}")
        logger.info(f"  Threshold: {ANOMALY_THRESHOLD}")
        logger.info(f"  Poll Interval: {POLL_INTERVAL}s")

        try:
            while True:
                try:
                    await self.process_batch()
                except Exception as e:
                    logger.error(f"Error in processing batch: {e}", exc_info=True)

                # Wait before next poll
                await asyncio.sleep(POLL_INTERVAL)

        finally:
            await self.store.close()


async def main():
    """Main entry point"""
    service = Layer1FilterService()
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())
