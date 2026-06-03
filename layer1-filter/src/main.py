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
from typing import Dict, List

from filters.pattern_filter import PatternFilter
from filters.base import FilterResult
from storage.anomaly_store import AnomalyStore

try:
    from filters.logbert_filter import LogBERTFilter
except ModuleNotFoundError as e:
    LogBERTFilter = None
    LOGBERT_IMPORT_ERROR = e
else:
    LOGBERT_IMPORT_ERROR = None

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
LOKI_URL = os.getenv('LOKI_URL', 'http://loki:3100')
LOKI_TENANT = os.getenv('LOKI_TENANT', 'raw')
LOKI_QUERY = os.getenv('LOKI_QUERY', '{source="target-nginx"}')
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '10'))
INITIAL_LOOKBACK_SECONDS = int(os.getenv('INITIAL_LOOKBACK_SECONDS', '300'))

DB_HOST = os.getenv('DB_HOST', 'timescaledb')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'logdb')
DB_USER = os.getenv('DB_USER', 'logdb')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'logdb_password')

ANOMALY_THRESHOLD = float(os.getenv('ANOMALY_THRESHOLD', '0.5'))
BATCH_SIZE = int(os.getenv('BATCH_SIZE', '50'))
NODE_ID = os.getenv('AADS_NODE_ID', os.getenv('NODE_ID', 'controller'))
ENABLE_PATTERN_FILTER = os.getenv('ENABLE_PATTERN_FILTER', 'true').lower() == 'true'
ENABLE_LOGBERT_FILTER = os.getenv('ENABLE_LOGBERT_FILTER', 'true').lower() == 'true'


def _next_cursor_timestamp(logs: List[Dict], fallback: datetime) -> datetime:
    timestamps = [log.get('timestamp') for log in logs if log.get('timestamp')]
    return max(timestamps) if timestamps else fallback


class Layer1FilterService:
    """Main service for Layer 1 anomaly filtering"""

    def __init__(self):
        self.pattern_filter = PatternFilter(enabled=ENABLE_PATTERN_FILTER)
        self.logbert_filter = None
        if ENABLE_LOGBERT_FILTER and LogBERTFilter:
            self.logbert_filter = LogBERTFilter(
                model_path='bert-base-uncased',
                threshold=ANOMALY_THRESHOLD,
                device='cpu'  # Use GPU if available
            )
        elif ENABLE_LOGBERT_FILTER:
            logger.warning(f"LogBERT disabled because dependencies are unavailable: {LOGBERT_IMPORT_ERROR}")

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
                                    'node_id': labels.get('node_id', NODE_ID),
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
            start_time = end_time - timedelta(seconds=INITIAL_LOOKBACK_SECONDS)

        # Fetch logs
        logs = await self.fetch_logs_from_loki(start_time, end_time)

        if not logs:
            logger.debug("No new logs to process")
            return

        logger.info(f"Processing {len(logs)} logs...")

        # Run deterministic pattern OR LogBERT. Pattern matching keeps lab
        # scenarios stable, while LogBERT remains the semantic detector.
        results = self._run_fusion(logs)

        # Filter anomalies
        anomalies = [r for r in results if r.is_anomaly]

        if anomalies:
            logger.info(f"Detected {len(anomalies)} anomalies (threshold: {ANOMALY_THRESHOLD})")

            # Store to PostgreSQL
            await self.store.store_batch(anomalies)

            self.stats['total_anomalies'] += len(anomalies)

        self.stats['total_processed'] += len(logs)
        self.last_timestamp = _next_cursor_timestamp(logs, end_time)

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
        logger.info(f"  Initial Lookback: {INITIAL_LOOKBACK_SECONDS}s")
        logger.info(f"  Node ID: {NODE_ID}")
        logger.info(f"  Pattern Filter: {ENABLE_PATTERN_FILTER}")
        logger.info(f"  LogBERT Filter: {ENABLE_LOGBERT_FILTER}")

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

    def _run_fusion(self, logs: List[Dict]) -> List[FilterResult]:
        stage_results: List[FilterResult] = []
        if self.pattern_filter.enabled:
            stage_results.extend(self.pattern_filter.predict(logs))

        if self.logbert_filter:
            try:
                stage_results.extend(self.logbert_filter.predict(logs))
            except Exception as e:
                logger.error(f"LogBERT failed; continuing with pattern results: {e}", exc_info=True)

        by_key: Dict[str, List[FilterResult]] = {}
        for result in stage_results:
            log = result.log
            key = f"{log.get('timestamp')}|{log.get('container')}|{log.get('message')}"
            by_key.setdefault(key, []).append(result)

        fused: List[FilterResult] = []
        for results in by_key.values():
            anomalies = [r for r in results if r.is_anomaly]
            if not anomalies:
                # Keep a normal result for stats compatibility if no stage fired.
                fused.append(results[0])
                continue

            log = anomalies[0].log
            stages = sorted({r.filter_stage for r in anomalies})
            metadata = {
                "fusion_rule": "pattern_or_logbert",
                "stages": {
                    r.filter_stage: {
                        "score": r.anomaly_score,
                        "metadata": r.metadata,
                    }
                    for r in anomalies
                },
            }
            fused.append(
                FilterResult(
                    log=log,
                    anomaly_score=max(r.anomaly_score for r in anomalies),
                    is_anomaly=True,
                    filter_stage="+".join(stages),
                    metadata=metadata,
                )
            )

        return fused


async def main():
    """Main entry point"""
    service = Layer1FilterService()
    await service.run()


if __name__ == '__main__':
    asyncio.run(main())
