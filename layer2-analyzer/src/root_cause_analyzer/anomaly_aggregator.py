"""
Anomaly Aggregator - Cluster and aggregate anomalies

Groups related anomalies together to identify patterns and root causes.
"""
import logging
from typing import List, Dict, Any, Set
from datetime import datetime, timedelta
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AnomalyCluster:
    """Represents a cluster of related anomalies"""
    cluster_id: str
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    containers: Set[str] = field(default_factory=set)
    services: Set[str] = field(default_factory=set)
    templates: Set[str] = field(default_factory=set)
    start_time: datetime = None
    end_time: datetime = None
    total_count: int = 0
    avg_score: float = 0.0
    max_score: float = 0.0

    def add_anomaly(self, anomaly: Dict[str, Any]):
        """Add an anomaly to the cluster"""
        self.anomalies.append(anomaly)
        self.containers.add(anomaly.get('container', 'unknown'))
        self.services.add(anomaly.get('service', 'unknown'))
        if anomaly.get('template'):
            self.templates.add(anomaly['template'])

        # Update time range
        anomaly_time = anomaly.get('time')
        if isinstance(anomaly_time, str):
            anomaly_time = datetime.fromisoformat(anomaly_time)

        if self.start_time is None or anomaly_time < self.start_time:
            self.start_time = anomaly_time
        if self.end_time is None or anomaly_time > self.end_time:
            self.end_time = anomaly_time

        # Update scores
        score = anomaly.get('anomaly_score', 0.0)
        self.total_count = len(self.anomalies)
        self.avg_score = (self.avg_score * (self.total_count - 1) + score) / self.total_count
        self.max_score = max(self.max_score, score)

    def to_dict(self) -> Dict[str, Any]:
        """Convert cluster to dictionary"""
        return {
            "cluster_id": self.cluster_id,
            "containers": list(self.containers),
            "services": list(self.services),
            "templates": list(self.templates),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": (self.end_time - self.start_time).total_seconds() if self.start_time and self.end_time else 0,
            "total_count": self.total_count,
            "avg_score": self.avg_score,
            "max_score": self.max_score,
            "anomalies": self.anomalies
        }


class AnomalyAggregator:
    """
    Aggregate and cluster anomalies

    Strategies:
    1. Time-based clustering (events within N minutes)
    2. Container/service grouping
    3. Template similarity
    4. Cascade failure detection
    """

    def __init__(
        self,
        time_window_minutes: int = 5,
        min_cluster_size: int = 2
    ):
        """
        Initialize anomaly aggregator

        Args:
            time_window_minutes: Time window for clustering (minutes)
            min_cluster_size: Minimum anomalies to form a cluster
        """
        self.time_window = timedelta(minutes=time_window_minutes)
        self.min_cluster_size = min_cluster_size

        self.stats = {
            "total_anomalies_processed": 0,
            "total_clusters_created": 0
        }

    def aggregate(self, anomalies: List[Dict[str, Any]]) -> List[AnomalyCluster]:
        """
        Aggregate anomalies into clusters

        Args:
            anomalies: List of anomaly dictionaries

        Returns:
            List of AnomalyCluster objects
        """
        if not anomalies:
            return []

        logger.info(f"Aggregating {len(anomalies)} anomalies...")

        # Sort by time
        sorted_anomalies = sorted(
            anomalies,
            key=lambda x: x.get('time') if isinstance(x.get('time'), datetime) else datetime.fromisoformat(str(x.get('time')))
        )

        # Cluster by time window and container/service
        clusters: Dict[str, AnomalyCluster] = {}
        cluster_counter = 0

        for anomaly in sorted_anomalies:
            anomaly_time = anomaly.get('time')
            if isinstance(anomaly_time, str):
                anomaly_time = datetime.fromisoformat(anomaly_time)

            container = anomaly.get('container', 'unknown')
            service = anomaly.get('service', 'unknown')

            # Find matching cluster
            matched_cluster = None
            for cluster in clusters.values():
                # Check if within time window
                if cluster.end_time and abs((anomaly_time - cluster.end_time).total_seconds()) <= self.time_window.total_seconds():
                    # Check if same container or service
                    if container in cluster.containers or service in cluster.services:
                        matched_cluster = cluster
                        break

            if matched_cluster:
                matched_cluster.add_anomaly(anomaly)
            else:
                # Create new cluster
                cluster_id = f"cluster_{cluster_counter}"
                cluster_counter += 1
                cluster = AnomalyCluster(cluster_id=cluster_id)
                cluster.add_anomaly(anomaly)
                clusters[cluster_id] = cluster

        # Filter by minimum size
        valid_clusters = [
            c for c in clusters.values()
            if c.total_count >= self.min_cluster_size
        ]

        # Update statistics
        self.stats["total_anomalies_processed"] += len(anomalies)
        self.stats["total_clusters_created"] += len(valid_clusters)

        logger.info(
            f"Created {len(valid_clusters)} clusters from {len(anomalies)} anomalies "
            f"(min size: {self.min_cluster_size})"
        )

        return valid_clusters

    def detect_cascade_failures(self, clusters: List[AnomalyCluster]) -> List[Dict[str, Any]]:
        """
        Detect cascade failures across services

        A cascade failure is when anomalies spread across multiple services
        in a time-ordered manner.

        Args:
            clusters: List of anomaly clusters

        Returns:
            List of cascade failure patterns
        """
        cascades = []

        # Sort clusters by start time
        sorted_clusters = sorted(clusters, key=lambda c: c.start_time)

        # Look for sequences where different services fail in order
        for i in range(len(sorted_clusters) - 1):
            current = sorted_clusters[i]
            next_cluster = sorted_clusters[i + 1]

            # Check if services are different and time is close
            if current.services != next_cluster.services:
                time_diff = (next_cluster.start_time - current.start_time).total_seconds()
                if 0 < time_diff <= 300:  # Within 5 minutes
                    cascades.append({
                        "type": "cascade_failure",
                        "origin_cluster": current.cluster_id,
                        "origin_services": list(current.services),
                        "target_cluster": next_cluster.cluster_id,
                        "target_services": list(next_cluster.services),
                        "time_diff_seconds": time_diff
                    })

        if cascades:
            logger.warning(f"Detected {len(cascades)} potential cascade failures")

        return cascades

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregator statistics"""
        return dict(self.stats)
