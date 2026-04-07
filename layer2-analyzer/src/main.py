"""
Main Analyzer - Integrates all Layer 2 & Layer 3 components

Orchestrates the complete AI auto-debug pipeline:
1. Consume anomalies from PostgreSQL
2. Aggregate into clusters
3. Correlate with Prometheus metrics
4. Fetch RAW log context
5. Search knowledge base
6. LLM root cause analysis
7. Generate remediation suggestions (Layer 3)
8. Send notifications (Layer 3)
9. Store diagnosis reports
"""
import os
import asyncio
import json
import logging
from typing import List, Dict, Any
from datetime import datetime
import asyncpg

from anomaly_consumer import AnomalyConsumer
from raw_log_fetcher import RawLogFetcher
from root_cause_analyzer import (
    AnomalyAggregator,
    MetricsCorrelator,
    KnowledgeBase,
    LLMReasoner
)
from llm.openai_compatible import OpenAICompatibleClient
from llm.ollama import OllamaClient
from suggestion_generator import SuggestionGenerator
from notification_hub import NotificationHub

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class RootCauseAnalyzer:
    """
    Main root cause analyzer

    Orchestrates the analysis pipeline
    """

    def __init__(self):
        """Initialize analyzer with configuration from environment"""
        # Database connection
        self.db_config = {
            "host": os.getenv("DB_HOST", "timescaledb"),
            "port": int(os.getenv("DB_PORT", "5432")),
            "database": os.getenv("DB_NAME", "logdb"),
            "user": os.getenv("DB_USER", "logdb"),
            "password": os.getenv("DB_PASSWORD", "logdb_password")
        }

        # Initialize components
        self.consumer = AnomalyConsumer(**self.db_config)
        self.raw_log_fetcher = RawLogFetcher(**self.db_config)
        self.aggregator = AnomalyAggregator(
            time_window_minutes=int(os.getenv("TIME_WINDOW_MINUTES", "5")),
            min_cluster_size=int(os.getenv("MIN_CLUSTER_SIZE", "2"))
        )
        self.metrics_correlator = MetricsCorrelator(
            prometheus_url=os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
        )
        self.knowledge_base = KnowledgeBase(
            db_path=os.getenv("VECTOR_DB_PATH", "/app/data/chromadb")
        )

        # Initialize LLM client
        llm_strategy = os.getenv("LLM_STRATEGY", "cascade")
        llm_provider = os.getenv("LLM_PROVIDER", "openai_compatible")

        if llm_provider == "ollama":
            self.llm_client = OllamaClient(
                base_url=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"),
                model=os.getenv("OLLAMA_MODEL", "llama3.2:3b")
            )
        else:  # openai_compatible
            self.llm_client = OpenAICompatibleClient(
                api_key=os.getenv("LLM_API_KEY", ""),
                base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                model=os.getenv("LLM_MODEL", "gpt-4o-mini")
            )

        self.reasoner = LLMReasoner(self.llm_client, strategy=llm_strategy)

        # Layer 3: Remediation
        self.suggestion_generator = SuggestionGenerator(
            auto_remediation_enabled=os.getenv("AUTO_REMEDIATION_ENABLED", "false").lower() == "true"
        )
        self.notification_hub = NotificationHub(
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            custom_webhook_url=os.getenv("WEBHOOK_URL"),
            severity_filter=os.getenv("SEVERITY_FILTER", "medium")
        )

        # Database pool for storing diagnoses
        self.db_pool = None

    async def init_db_pool(self):
        """Initialize database connection pool for diagnosis storage"""
        if self.db_pool:
            return

        self.db_pool = await asyncpg.create_pool(**self.db_config, min_size=2, max_size=10)
        logger.info("Database pool for diagnosis storage created")

    async def close_db_pool(self):
        """Close database connection pool"""
        if self.db_pool:
            await self.db_pool.close()
            self.db_pool = None

    async def process_anomaly_batch(self, anomalies: List[Dict[str, Any]]):
        """
        Process a batch of anomalies

        Args:
            anomalies: List of anomaly dictionaries from consumer
        """
        logger.info(f"Processing batch of {len(anomalies)} anomalies...")

        # Step 1: Aggregate anomalies into clusters
        clusters = self.aggregator.aggregate(anomalies)

        if not clusters:
            logger.info("No clusters formed (anomalies below minimum cluster size)")
            return

        logger.info(f"Formed {len(clusters)} clusters")

        # Step 2: Analyze each cluster
        for cluster in clusters:
            try:
                await self.analyze_cluster(cluster)
            except Exception as e:
                logger.error(f"Error analyzing cluster {cluster.cluster_id}: {e}", exc_info=True)

    async def analyze_cluster(self, cluster):
        """
        Analyze a single anomaly cluster

        Args:
            cluster: AnomalyCluster object
        """
        logger.info(f"Analyzing cluster: {cluster.cluster_id}")

        # ============================================
        # Deduplication: Check for similar diagnosis
        # ============================================
        similar_diagnosis = await self.find_similar_diagnosis(cluster)
        if similar_diagnosis:
            logger.info(
                f"Found similar diagnosis {similar_diagnosis['diagnosis_id']} "
                f"for cluster {cluster.cluster_id}, reusing diagnosis (saving LLM tokens)"
            )
            # Reuse diagnosis with updated metadata
            diagnosis = self._reuse_diagnosis(similar_diagnosis, cluster)

            # Run Layer 3 for notifications (respects severity filter)
            await self._run_layer3(diagnosis)

            await self.store_diagnosis(diagnosis)
            logger.info(
                f"Diagnosis reused: {diagnosis['diagnosis_id']} "
                f"(severity: {diagnosis['severity']})"
            )
            return

        # Get representative container for metrics
        container = list(cluster.containers)[0] if cluster.containers else None

        # Step 1: Correlate metrics
        metrics_context = {}
        if container:
            try:
                metrics_context = await self.metrics_correlator.correlate_metrics(
                    anomaly_time=cluster.start_time,
                    container=container,
                    context_minutes=5
                )
            except Exception as e:
                logger.error(f"Error correlating metrics: {e}")

        # Step 2: Fetch RAW log context
        raw_logs = []
        if container:
            try:
                raw_logs = await self.raw_log_fetcher.fetch_context_logs(
                    timestamp=cluster.start_time,
                    container=container,
                    before_minutes=5,
                    after_minutes=5,
                    limit=100
                )
            except Exception as e:
                logger.error(f"Error fetching raw logs: {e}")

        # Step 3: Search knowledge base
        # Create query from templates
        query_text = "\n".join(list(cluster.templates)[:5])
        similar_cases = self.knowledge_base.search_similar_cases(
            query=query_text,
            n_results=3,
            min_effectiveness=0.5
        )

        # Step 4: LLM root cause analysis
        diagnosis = await self.reasoner.analyze(
            cluster=cluster,
            metrics_context=metrics_context,
            similar_cases=similar_cases,
            raw_logs=raw_logs
        )

        # Step 5-6: Layer 3 - Generate suggestions and send notifications
        await self._run_layer3(diagnosis)

        # Step 7: Store diagnosis
        await self.store_diagnosis(diagnosis)

        logger.info(
            f"Diagnosis complete: {diagnosis['diagnosis_id']} "
            f"(severity: {diagnosis['severity']})"
        )

    async def find_similar_diagnosis(self, cluster) -> Dict[str, Any]:
        """
        Find similar diagnosis to avoid duplicate LLM analysis

        Similarity criteria:
        - Same container(s)
        - Similar templates (>70% overlap)
        - Within last 24 hours

        Args:
            cluster: AnomalyCluster to check

        Returns:
            Similar diagnosis dict if found, None otherwise
        """
        if not self.db_pool:
            await self.init_db_pool()

        # Create cluster signature from templates and containers
        cluster_containers = set(cluster.containers)
        cluster_templates = set(cluster.templates) if cluster.templates else set()

        # Need at least containers to do deduplication
        if not cluster_containers:
            logger.info(f"Skipping dedup check for {cluster.cluster_id}: no containers")
            return None

        logger.info(
            f"Checking similarity for {cluster.cluster_id}: "
            f"containers={cluster_containers}, templates_count={len(cluster_templates)}"
        )

        # Query recent diagnoses from same container(s)
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT diagnosis_id, timestamp, severity, summary,
                       root_cause, recommended_actions, affected_services
                FROM diagnosis_reports
                WHERE timestamp > NOW() - INTERVAL '24 hours'
                ORDER BY timestamp DESC
                LIMIT 50
                """
            )

        logger.debug(f"Found {len(rows)} recent diagnoses to check")

        # Check similarity for each recent diagnosis
        for row in rows:
            try:
                # Parse affected services (asyncpg returns dict directly for JSONB)
                affected_services = row['affected_services']

                # Extract containers from affected services
                diagnosis_containers = set()
                if isinstance(affected_services, (list, str)):
                    if isinstance(affected_services, str):
                        affected_services = json.loads(affected_services)

                    for service in affected_services:
                        if isinstance(service, dict) and 'container' in service:
                            diagnosis_containers.add(service['container'])

                # Check container overlap
                if not cluster_containers & diagnosis_containers:
                    continue  # No common containers

                # Calculate template similarity (if we had templates in diagnosis, we'd check here)
                # For now, if same container = likely similar issue
                container_overlap = len(cluster_containers & diagnosis_containers) / len(cluster_containers)

                logger.debug(
                    f"Checking {row['diagnosis_id']}: "
                    f"containers={diagnosis_containers}, overlap={container_overlap:.0%}"
                )

                if container_overlap >= 0.7:  # 70% container overlap
                    logger.info(
                        f"Found similar diagnosis: {row['diagnosis_id']} "
                        f"(container overlap: {container_overlap:.0%}, containers: {diagnosis_containers})"
                    )
                    return {
                        'diagnosis_id': row['diagnosis_id'],
                        'timestamp': row['timestamp'],
                        'severity': row['severity'],
                        'summary': row['summary'],
                        'root_cause': row['root_cause'] if isinstance(row['root_cause'], dict) else json.loads(row['root_cause']),
                        'recommended_actions': row['recommended_actions'] if isinstance(row['recommended_actions'], list) else json.loads(row['recommended_actions']),
                        'affected_services': affected_services
                    }

            except Exception as e:
                logger.warning(f"Error checking diagnosis {row.get('diagnosis_id', 'unknown')} similarity: {e}")
                continue

        logger.debug("No similar diagnosis found")
        return None

    def _reuse_diagnosis(self, similar_diagnosis: Dict[str, Any], cluster) -> Dict[str, Any]:
        """
        Reuse a similar diagnosis with updated metadata

        Args:
            similar_diagnosis: Previously found diagnosis
            cluster: Current cluster

        Returns:
            Updated diagnosis dictionary
        """
        # Extract original summary by removing all "(similar to xxx)" suffixes
        summary = similar_diagnosis['summary']
        import re
        # Remove all "(similar to ...)" patterns to get clean summary
        clean_summary = re.sub(r'\s*\(similar to [^)]+\)', '', summary)

        return {
            'diagnosis_id': f"diag_{cluster.cluster_id}_{int(datetime.now().timestamp())}_reused",
            'timestamp': datetime.now(),
            'cluster_id': cluster.cluster_id,
            'severity': similar_diagnosis['severity'],
            'summary': clean_summary,  # Use clean summary without suffixes
            'root_cause': similar_diagnosis['root_cause'],
            'recommended_actions': similar_diagnosis['recommended_actions'],
            'affected_services': [
                {
                    "container": container,
                    "anomaly_count": cluster.total_count,
                    "first_seen": cluster.start_time,
                    "last_seen": cluster.end_time
                }
                for container in cluster.containers
            ],
            'correlated_metrics': {},
            'reused_from': similar_diagnosis['diagnosis_id']
        }

    async def _run_layer3(self, diagnosis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute Layer 3 processing: generate suggestions and send notifications

        Args:
            diagnosis: Diagnosis dictionary (from LLM or reused)

        Returns:
            Dictionary with suggestions and notification_results
        """
        # Generate suggestions
        suggestions = self.suggestion_generator.generate_suggestions(diagnosis)
        logger.info(f"Generated {len(suggestions)} suggestions")

        # Format and send notifications
        formatted_message = self.suggestion_generator.format_for_notification(
            diagnosis, suggestions
        )
        notification_results = await self.notification_hub.send_notification(
            diagnosis=diagnosis,
            suggestions=suggestions,
            formatted_message=formatted_message
        )
        if notification_results:
            logger.info(f"Notifications sent: {notification_results}")

        return {
            'suggestions': suggestions,
            'notification_results': notification_results
        }

    async def store_diagnosis(self, diagnosis: Dict[str, Any]):
        """
        Store diagnosis report in database

        Args:
            diagnosis: Diagnosis dictionary
        """
        if not self.db_pool:
            await self.init_db_pool()

        # Custom JSON encoder for datetime objects
        def json_serial(obj):
            """JSON serializer for objects not serializable by default json code"""
            if isinstance(obj, datetime):
                return obj.isoformat()
            raise TypeError(f"Type {type(obj)} not serializable")

        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO diagnosis_reports
                (diagnosis_id, timestamp, severity, summary, root_cause,
                 affected_services, correlated_metrics, recommended_actions)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                diagnosis['diagnosis_id'],
                diagnosis['timestamp'],
                diagnosis['severity'],
                diagnosis['summary'],
                json.dumps(diagnosis.get('root_cause', {}), default=json_serial),
                json.dumps(diagnosis.get('affected_services', []), default=json_serial),
                json.dumps(diagnosis.get('correlated_metrics', {}), default=json_serial),
                json.dumps(diagnosis.get('recommended_actions', []), default=json_serial)
            )

        logger.info(f"Stored diagnosis: {diagnosis['diagnosis_id']}")

    async def run(self):
        """Run analyzer continuously"""
        logger.info("Starting Root Cause Analyzer...")

        # Set consumer callback
        self.consumer.set_callback(self.process_anomaly_batch)

        # Initialize database pool
        await self.init_db_pool()

        try:
            # Run consumer
            await self.consumer.run_forever()
        finally:
            await self.close_db_pool()
            self.knowledge_base.persist()


async def main():
    """Main entry point"""
    analyzer = RootCauseAnalyzer()
    await analyzer.run()


if __name__ == '__main__':
    asyncio.run(main())
