"""
LLM Reasoner - Root cause analysis using LLM

Integrates all analysis components and uses LLM for reasoning.
"""
import logging
import json
from typing import List, Dict, Any
from datetime import datetime

from llm.base import LLMMessage
from llm.openai_compatible import OpenAICompatibleClient
from llm.ollama import OllamaClient
from root_cause_analyzer.anomaly_aggregator import AnomalyCluster

logger = logging.getLogger(__name__)


class LLMReasoner:
    """
    LLM-based root cause analysis

    Uses LLM to:
    1. Analyze anomaly patterns
    2. Correlate with metrics
    3. Search knowledge base
    4. Generate diagnosis report
    """

    def __init__(
        self,
        llm_client,
        strategy: str = "cascade"
    ):
        """
        Initialize LLM reasoner

        Args:
            llm_client: LLM client instance (OpenAICompatibleClient or OllamaClient)
            strategy: 'cascade', 'primary_only', or 'local_only'
        """
        self.llm_client = llm_client
        self.strategy = strategy

        self.stats = {
            "total_diagnoses": 0,
            "total_tokens_used": 0
        }

    async def analyze(
        self,
        cluster: AnomalyCluster,
        metrics_context: Dict[str, Any],
        similar_cases: List[Dict[str, Any]],
        raw_logs: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Perform root cause analysis

        Args:
            cluster: Anomaly cluster to analyze
            metrics_context: Correlated metrics
            similar_cases: Similar historical cases from knowledge base
            raw_logs: Optional raw log context

        Returns:
            Diagnosis dictionary
        """
        logger.info(f"Analyzing cluster {cluster.cluster_id} with LLM...")

        # Build analysis prompt
        prompt = self._build_analysis_prompt(cluster, metrics_context, similar_cases, raw_logs)

        # Call LLM
        messages = [
            LLMMessage(role="system", content=self._get_system_prompt()),
            LLMMessage(role="user", content=prompt)
        ]

        response = await self.llm_client.complete(messages, temperature=0.3, max_tokens=2048)

        # Parse response
        try:
            diagnosis = self._parse_diagnosis(response.content, cluster)
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
            diagnosis = self._fallback_diagnosis(cluster)

        # Update statistics
        self.stats["total_diagnoses"] += 1
        self.stats["total_tokens_used"] += response.tokens_used

        logger.info(
            f"Diagnosis complete for {cluster.cluster_id}: "
            f"severity={diagnosis['severity']}, confidence={diagnosis['root_cause']['confidence']}"
        )

        return diagnosis

    def _get_system_prompt(self) -> str:
        """Get system prompt for LLM"""
        return """You are an expert system administrator and DevOps engineer specializing in log analysis and root cause diagnosis.

Your task is to analyze anomalous logs and system metrics to determine:
1. What went wrong (root cause)
2. Why it happened (contributing factors)
3. What should be done (recommended actions)

Provide your analysis in JSON format with the following structure:
{
  "severity": "low|medium|high|critical",
  "summary": "Brief one-sentence summary",
  "root_cause": {
    "category": "dependency_failure|resource_exhaustion|configuration_error|network_issue|other",
    "description": "Detailed description of the root cause",
    "confidence": 0.0-1.0
  },
  "contributing_factors": ["factor1", "factor2"],
  "evidence": ["evidence1", "evidence2"],
  "recommended_actions": [
    {"priority": 1, "action": "action_name", "description": "what to do"}
  ]
}

Be concise, precise, and actionable. Focus on facts from the data provided."""

    def _build_analysis_prompt(
        self,
        cluster: AnomalyCluster,
        metrics_context: Dict[str, Any],
        similar_cases: List[Dict[str, Any]],
        raw_logs: List[Dict[str, Any]] = None
    ) -> str:
        """Build analysis prompt for LLM"""
        prompt_parts = []

        # Cluster summary
        prompt_parts.append("## Anomaly Cluster Summary")
        prompt_parts.append(f"- Cluster ID: {cluster.cluster_id}")
        prompt_parts.append(f"- Time Range: {cluster.start_time} to {cluster.end_time}")
        prompt_parts.append(f"- Duration: {(cluster.end_time - cluster.start_time).total_seconds():.0f} seconds")
        prompt_parts.append(f"- Total Anomalies: {cluster.total_count}")
        prompt_parts.append(f"- Average Score: {cluster.avg_score:.2f}")
        prompt_parts.append(f"- Max Score: {cluster.max_score:.2f}")
        prompt_parts.append(f"- Affected Containers: {', '.join(cluster.containers)}")
        prompt_parts.append(f"- Affected Services: {', '.join(cluster.services)}")

        # Anomaly patterns
        prompt_parts.append("\n## Anomaly Log Templates")
        for template in list(cluster.templates)[:10]:  # Limit to 10 templates
            prompt_parts.append(f"- {template}")

        # Sample anomalies
        prompt_parts.append("\n## Sample Anomalous Logs (first 5)")
        for i, anomaly in enumerate(cluster.anomalies[:5]):
            prompt_parts.append(f"{i+1}. [{anomaly.get('time')}] {anomaly.get('raw_message', '')[:200]}")

        # Metrics context
        if metrics_context:
            prompt_parts.append("\n## Correlated Metrics")
            analysis = metrics_context.get('analysis', {})
            if any(analysis.values()):
                prompt_parts.append("**Detected Issues:**")
                if analysis.get('cpu_spike'):
                    prompt_parts.append(f"- CPU Spike: {metrics_context.get('cpu', 0)*100:.1f}%")
                if analysis.get('memory_pressure'):
                    mem_gb = metrics_context.get('memory_bytes', 0) / (1024**3)
                    prompt_parts.append(f"- Memory Pressure: {mem_gb:.2f} GB")
                if analysis.get('network_spike'):
                    prompt_parts.append("- Network Spike Detected")
                if analysis.get('gpu_overload'):
                    gpu_util = metrics_context.get('gpu_utilization', 0)
                    prompt_parts.append(f"- GPU Overload: {gpu_util:.1f}%")

        # Similar cases
        if similar_cases:
            prompt_parts.append("\n## Similar Historical Cases")
            for i, case in enumerate(similar_cases[:3]):  # Limit to 3 cases
                prompt_parts.append(f"\n### Case {i+1} (Effectiveness: {case['effectiveness']:.2f})")
                prompt_parts.append(f"- Root Cause: {case['root_cause']}")
                prompt_parts.append(f"- Resolution: {case['resolution']}")

        # Raw log context
        if raw_logs:
            prompt_parts.append(f"\n## Raw Log Context (sample of {len(raw_logs)} logs)")
            for i, log in enumerate(raw_logs[:5]):
                prompt_parts.append(f"{i+1}. [{log.get('time')}] {log.get('message', '')[:150]}")

        prompt_parts.append("\n## Your Task")
        prompt_parts.append("Analyze the above information and provide a comprehensive diagnosis in JSON format.")

        return "\n".join(prompt_parts)

    def _parse_diagnosis(self, llm_response: str, cluster: AnomalyCluster) -> Dict[str, Any]:
        """Parse LLM response into diagnosis structure"""
        # Try to extract JSON from response
        try:
            # Find JSON in response (might be wrapped in markdown code blocks)
            start = llm_response.find('{')
            end = llm_response.rfind('}') + 1
            if start >= 0 and end > start:
                json_str = llm_response[start:end]
                diagnosis_data = json.loads(json_str)
            else:
                raise ValueError("No JSON found in response")

            # Add metadata
            diagnosis_data['diagnosis_id'] = f"diag_{cluster.cluster_id}_{int(datetime.now().timestamp())}"
            diagnosis_data['timestamp'] = datetime.now()
            diagnosis_data['cluster_id'] = cluster.cluster_id
            diagnosis_data['affected_services'] = [
                {
                    "container": container,
                    "anomaly_count": cluster.total_count,
                    "first_seen": cluster.start_time,
                    "last_seen": cluster.end_time
                }
                for container in cluster.containers
            ]

            return diagnosis_data

        except Exception as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            raise

    def _fallback_diagnosis(self, cluster: AnomalyCluster) -> Dict[str, Any]:
        """Generate fallback diagnosis if LLM fails"""
        return {
            "diagnosis_id": f"diag_{cluster.cluster_id}_fallback",
            "timestamp": datetime.now(),
            "cluster_id": cluster.cluster_id,
            "severity": "medium",
            "summary": f"Anomalies detected in {', '.join(cluster.containers)}",
            "root_cause": {
                "category": "unknown",
                "description": "Unable to determine root cause (LLM analysis failed)",
                "confidence": 0.0
            },
            "contributing_factors": [],
            "evidence": [f"Template: {t}" for t in list(cluster.templates)[:3]],
            "recommended_actions": [
                {
                    "priority": 1,
                    "action": "manual_investigation",
                    "description": "Manually investigate the anomalous logs"
                }
            ],
            "affected_services": [
                {
                    "container": container,
                    "anomaly_count": cluster.total_count,
                    "first_seen": cluster.start_time,
                    "last_seen": cluster.end_time
                }
                for container in cluster.containers
            ]
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get reasoner statistics"""
        return dict(self.stats)
