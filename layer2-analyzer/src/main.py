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
10. Expose webhook for event-driven anomaly ingestion
"""
import os
import asyncio
import json
import logging
from typing import List, Dict, Any
from datetime import datetime
import asyncpg
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn

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

# New: Map-Reduce and Agent imports
from aggregator.map_reduce import deduplicate_and_summarize, format_summary_for_prompt
from agent.graph import run_agent_analysis
from schemas.action_plan import ClaudeStylePlan

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

        # Knowledge Base (optional - disabled if ENABLE_KNOWLEDGE_BASE=false)
        enable_kb = os.getenv("ENABLE_KNOWLEDGE_BASE", "false").lower() == "true"
        if enable_kb:
            logger.info("Knowledge Base enabled, initializing...")
            self.knowledge_base = KnowledgeBase(
                db_path=os.getenv("VECTOR_DB_PATH", "/app/data/chromadb")
            )
        else:
            logger.info("Knowledge Base disabled (ENABLE_KNOWLEDGE_BASE=false)")
            self.knowledge_base = None

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

        # ============================================================
        # NEW: Map-Reduce aggregation from ALL containers
        # ============================================================
        logger.info(f"Aggregating data from {len(cluster.containers)} containers...")

        cluster_data = {
            'containers': cluster.containers,
            'anomalies': cluster.anomalies,
            'start_time': cluster.start_time,
            'end_time': cluster.end_time,
            'templates': cluster.templates
        }

        # Aggregate and deduplicate data from all containers
        summary = deduplicate_and_summarize(cluster_data)
        summary_text = format_summary_for_prompt(summary)

        logger.info(
            f"Map-Reduce complete: {len(summary['template_summary'])} templates, "
            f"severity={summary['severity']}"
        )

        # ============================================================
        # NEW: LangGraph Agent investigation
        # ============================================================
        logger.info("Starting Agent investigation...")

        # Build initial prompt with Map-Reduce summary
        initial_prompt = f"""
You are an expert SRE investigating system anomalies.

{summary_text}

Your task:
1. Investigate the anomalies using available tools (query_loki, query_prometheus, execute_diagnostic_command)
2. Gather additional context as needed
3. Determine the root cause
4. When ready, signal that you have enough information to generate an action plan

Available tools:
- query_loki(query, start_time, end_time): Query logs using LogQL
- query_prometheus(promql): Query metrics using PromQL
- execute_diagnostic_command(command): Execute read-only Linux diagnostic commands

Time range: {summary['time_range']['start']} to {summary['time_range']['end']}
"""

        try:
            # Run Agent analysis
            action_plan: ActionPlan = await run_agent_analysis(initial_prompt)

            logger.info(
                f"Agent analysis complete: goal={action_plan.goal[:100]}..., "
                f"confidence={action_plan.confidence_score}, "
                f"steps={len(action_plan.execution_steps)}"
            )

            # Convert ClaudeStylePlan to diagnosis format for compatibility with Layer 3
            diagnosis = {
                'diagnosis_id': f"diag_{cluster.cluster_id}_{int(datetime.now().timestamp())}",
                'timestamp': datetime.now(),
                'cluster_id': cluster.cluster_id,
                'severity': summary['severity'],
                'summary': action_plan.goal,  # Use goal as summary
                'root_cause': {
                    'description': action_plan.root_cause,
                    'confidence': action_plan.confidence_score
                },
                'action_plan': action_plan.model_dump(),  # Store full ClaudeStylePlan
                'affected_services': [
                    {
                        "container": container,
                        "anomaly_count": cluster.total_count,
                        "first_seen": cluster.start_time,
                        "last_seen": cluster.end_time
                    }
                    for container in cluster.containers
                ],
                'recommended_actions': [
                    {
                        'step_id': step.step_id,
                        'title': step.title,
                        'phase': step.phase,
                        'explanation': step.explanation,
                        'requires_approval': step.requires_approval,
                        'status': step.status,
                        'commands': [cmd.model_dump() for cmd in step.commands],
                        # Backward compatibility fields
                        'action_type': step.action_type,
                        'target': step.target,
                        'command': step.command,
                        'is_destructive': step.is_destructive,
                        'description': step.title  # Alias for old UI
                    }
                    for step in action_plan.execution_steps
                ],
                'correlated_metrics': {}
            }

        except Exception as e:
            logger.error(f"Error in Agent analysis: {e}", exc_info=True)
            # Fallback to safe diagnosis
            diagnosis = {
                'diagnosis_id': f"diag_{cluster.cluster_id}_{int(datetime.now().timestamp())}_fallback",
                'timestamp': datetime.now(),
                'cluster_id': cluster.cluster_id,
                'severity': summary['severity'],
                'summary': f"Anomalies detected but Agent analysis failed: {str(e)}",
                'root_cause': {
                    'description': 'Unable to determine root cause due to Agent error',
                    'confidence': 0.0
                },
                'action_plan': None,
                'affected_services': [
                    {
                        "container": container,
                        "anomaly_count": cluster.total_count,
                        "first_seen": cluster.start_time,
                        "last_seen": cluster.end_time
                    }
                    for container in cluster.containers
                ],
                'recommended_actions': [],
                'correlated_metrics': {}
            }

        # ============================================================
        # Layer 3: Generate suggestions and send notifications
        # ============================================================
        await self._run_layer3(diagnosis)

        # ============================================================
        # Store diagnosis
        # ============================================================
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
                       root_cause, recommended_actions, affected_services, action_plan
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
                        'action_plan': row['action_plan'] if row['action_plan'] else None,
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
            'action_plan': similar_diagnosis.get('action_plan'),  # Copy ClaudeStylePlan
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
            # Handle action_plan: ensure it's JSON string, not double-encoded
            action_plan_value = diagnosis.get('action_plan')
            if action_plan_value is not None:
                if isinstance(action_plan_value, str):
                    # Already a string, use as-is (don't double-encode)
                    action_plan_json = action_plan_value
                else:
                    # Dict or other object, serialize to JSON
                    action_plan_json = json.dumps(action_plan_value, default=json_serial)
            else:
                action_plan_json = None

            await conn.execute(
                """
                INSERT INTO diagnosis_reports
                (diagnosis_id, timestamp, severity, summary, root_cause,
                 affected_services, correlated_metrics, recommended_actions, action_plan)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                diagnosis['diagnosis_id'],
                diagnosis['timestamp'],
                diagnosis['severity'],
                diagnosis['summary'],
                json.dumps(diagnosis.get('root_cause', {}), default=json_serial),
                json.dumps(diagnosis.get('affected_services', []), default=json_serial),
                json.dumps(diagnosis.get('correlated_metrics', {}), default=json_serial),
                json.dumps(diagnosis.get('recommended_actions', []), default=json_serial),
                action_plan_json
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
            if self.knowledge_base:
                self.knowledge_base.persist()


# ============================================
# FastAPI Webhook for Event-Driven Ingestion
# ============================================

app = FastAPI(title="Layer 2 Analyzer API")

# Global analyzer instance
analyzer_instance: RootCauseAnalyzer = None


class AnomalyWebhookPayload(BaseModel):
    """Webhook payload from Alloy"""
    timestamp: str
    service: str
    log_message: str
    logbert_anomaly_score: float
    level: str = "INFO"


@app.on_event("startup")
async def startup_event():
    """Initialize analyzer on startup"""
    global analyzer_instance
    analyzer_instance = RootCauseAnalyzer()
    await analyzer_instance.init_db_pool()
    logger.info("FastAPI webhook server started")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    if analyzer_instance:
        await analyzer_instance.close_db_pool()
        if analyzer_instance.knowledge_base:
            analyzer_instance.knowledge_base.persist()
    logger.info("FastAPI webhook server stopped")


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "layer2-analyzer"}


@app.post("/api/webhooks/anomaly")
async def receive_anomaly_webhook(
    payload: AnomalyWebhookPayload,
    background_tasks: BackgroundTasks
):
    """
    Webhook endpoint to receive anomaly from Alloy fan-out
    Triggers immediate analysis
    """
    if not analyzer_instance:
        raise HTTPException(status_code=503, detail="Analyzer not initialized")

    logger.info(f"Received webhook: {payload.service} - score {payload.logbert_anomaly_score}")

    # Convert to anomaly dict format
    anomaly = {
        'timestamp': datetime.fromisoformat(payload.timestamp),
        'service': payload.service,
        'log_message': payload.log_message,
        'logbert_anomaly_score': payload.logbert_anomaly_score,
        'level': payload.level,
    }

    # Process in background to avoid blocking webhook response
    background_tasks.add_task(analyzer_instance.process_anomaly_batch, [anomaly])

    return {
        "status": "accepted",
        "message": "Anomaly queued for analysis",
        "timestamp": payload.timestamp
    }


async def run_analyzer():
    """Run analyzer consumer loop"""
    await analyzer_instance.run()


async def run_api_server():
    """Run FastAPI server"""
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Main entry point - run both analyzer and API server"""
    global analyzer_instance

    # Initialize analyzer instance
    analyzer_instance = RootCauseAnalyzer()
    await analyzer_instance.init_db_pool()
    logger.info("Analyzer instance initialized")

    # Create tasks for both services
    analyzer_task = asyncio.create_task(run_analyzer())
    api_task = asyncio.create_task(run_api_server())

    # Wait for both
    try:
        await asyncio.gather(analyzer_task, api_task)
    finally:
        await analyzer_instance.close_db_pool()
        if analyzer_instance.knowledge_base:
            analyzer_instance.knowledge_base.persist()


if __name__ == '__main__':
    asyncio.run(main())
