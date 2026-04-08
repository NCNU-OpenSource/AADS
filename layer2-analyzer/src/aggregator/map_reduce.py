"""
Map-Reduce aggregation for multi-container anomaly analysis

Solves the context-loss problem where only the first container's data was analyzed.
This module aggregates data from ALL containers in a cluster before sending to LLM.

Related: ADR-002, Phase 4.2
"""
import logging
from typing import Dict, List, Any, Set
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)


def deduplicate_and_summarize(cluster_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregate anomaly data from all containers in a cluster using Map-Reduce pattern

    Args:
        cluster_data: Dictionary containing:
            - containers: Set of container names
            - anomalies: List of anomaly dictionaries
            - start_time: Cluster start time
            - end_time: Cluster end time
            - templates: Set of anomaly templates

    Returns:
        Dictionary with deduplicated and summarized data:
            - template_summary: Dict[template, {count, affected_containers, sample_messages}]
            - affected_containers: List of all containers
            - time_range: {start, end, duration_seconds}
            - total_anomalies: Total count
            - severity: Estimated severity based on scope
    """
    containers = cluster_data.get('containers', set())
    anomalies = cluster_data.get('anomalies', [])
    templates = cluster_data.get('templates', set())
    start_time = cluster_data.get('start_time')
    end_time = cluster_data.get('end_time')

    logger.info(
        f"Starting Map-Reduce aggregation: {len(containers)} containers, "
        f"{len(anomalies)} anomalies, {len(templates)} templates"
    )

    # ============================================================
    # Map Phase: Group anomalies by template
    # ============================================================
    template_map: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        'count': 0,
        'affected_containers': set(),
        'sample_messages': [],
        'scores': []
    })

    for anomaly in anomalies:
        template = anomaly.get('template', 'unknown')
        container = anomaly.get('container', 'unknown')
        message = anomaly.get('raw_message', anomaly.get('log_message', ''))
        score = anomaly.get('anomaly_score', anomaly.get('logbert_anomaly_score', 0.0))

        template_map[template]['count'] += 1
        template_map[template]['affected_containers'].add(container)
        template_map[template]['scores'].append(score)

        # Keep first 3 sample messages per template
        if len(template_map[template]['sample_messages']) < 3:
            template_map[template]['sample_messages'].append({
                'container': container,
                'message': message[:200],  # Truncate long messages
                'score': score
            })

    # ============================================================
    # Reduce Phase: Aggregate template statistics
    # ============================================================
    template_summary = {}
    for template, data in template_map.items():
        template_summary[template] = {
            'count': data['count'],
            'affected_containers': sorted(list(data['affected_containers'])),
            'max_score': max(data['scores']) if data['scores'] else 0.0,
            'avg_score': sum(data['scores']) / len(data['scores']) if data['scores'] else 0.0,
            'sample_messages': data['sample_messages']
        }

    # ============================================================
    # Calculate overall severity
    # ============================================================
    severity = _calculate_severity(
        num_containers=len(containers),
        num_anomalies=len(anomalies),
        max_score=max((t['max_score'] for t in template_summary.values()), default=0.0)
    )

    # ============================================================
    # Build summary
    # ============================================================
    duration_seconds = 0
    if start_time and end_time:
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)
        duration_seconds = (end_time - start_time).total_seconds()

    summary = {
        'template_summary': template_summary,
        'affected_containers': sorted(list(containers)),
        'time_range': {
            'start': start_time.isoformat() if isinstance(start_time, datetime) else start_time,
            'end': end_time.isoformat() if isinstance(end_time, datetime) else end_time,
            'duration_seconds': duration_seconds
        },
        'total_anomalies': len(anomalies),
        'severity': severity
    }

    logger.info(
        f"Map-Reduce complete: {len(template_summary)} unique templates, "
        f"severity={severity}, affected={len(containers)} containers"
    )

    return summary


def _calculate_severity(num_containers: int, num_anomalies: int, max_score: float) -> str:
    """
    Calculate severity level based on scope and anomaly scores

    Args:
        num_containers: Number of affected containers
        num_anomalies: Total number of anomalies
        max_score: Maximum anomaly score

    Returns:
        Severity level: low, medium, high, critical
    """
    # Critical: High score + multiple containers
    if max_score > 0.8 and num_containers >= 3:
        return 'critical'

    # High: High score or many containers
    if max_score > 0.7 or num_containers >= 5:
        return 'high'

    # Medium: Moderate score or multiple containers
    if max_score > 0.5 or num_containers >= 2:
        return 'medium'

    # Low: Everything else
    return 'low'


def format_summary_for_prompt(summary: Dict[str, Any]) -> str:
    """
    Format Map-Reduce summary into a human-readable prompt section

    Args:
        summary: Output from deduplicate_and_summarize()

    Returns:
        Formatted string for LLM prompt
    """
    lines = []

    lines.append("## Aggregated Anomaly Summary (Map-Reduce)")
    lines.append("")

    # Overview
    lines.append(f"- **Severity**: {summary['severity']}")
    lines.append(f"- **Affected Containers**: {', '.join(summary['affected_containers'])}")
    lines.append(f"- **Total Anomalies**: {summary['total_anomalies']}")
    lines.append(f"- **Time Range**: {summary['time_range']['start']} → {summary['time_range']['end']}")
    lines.append(f"- **Duration**: {summary['time_range']['duration_seconds']:.0f} seconds")
    lines.append("")

    # Template breakdown
    lines.append("## Anomaly Templates (Deduplicated)")
    for template, data in summary['template_summary'].items():
        lines.append(f"\n### Template: `{template}`")
        lines.append(f"- **Count**: {data['count']} occurrences")
        lines.append(f"- **Containers**: {', '.join(data['affected_containers'])}")
        lines.append(f"- **Avg Score**: {data['avg_score']:.2f}, Max: {data['max_score']:.2f}")

        if data['sample_messages']:
            lines.append("- **Sample Messages**:")
            for idx, sample in enumerate(data['sample_messages'], 1):
                lines.append(f"  {idx}. [{sample['container']}] {sample['message']} (score: {sample['score']:.2f})")

    return "\n".join(lines)
