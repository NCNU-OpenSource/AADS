"""
Filter Pipeline - Cascading Multi-Layer Anomaly Detection

Chains multiple filters together in a pipeline for progressive anomaly detection.

Example pipeline:
    RF (coarse) → LogBERT (precise) → Other Transformer (advanced)

Each filter can be enabled/disabled and configured independently.
"""
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from filters.base import BaseFilter, FilterResult


@dataclass
class PipelineConfig:
    """Configuration for filter pipeline"""
    early_stopping: bool = False  # Stop if a filter marks log as normal
    aggregate_scores: bool = True  # Combine scores from all filters
    max_score: bool = True  # Use max score vs average


@dataclass
class PipelineStats:
    """Statistics for pipeline execution"""
    total_processed: int = 0
    total_anomalies: int = 0
    execution_time_ms: float = 0.0
    filter_stats: Dict[str, Dict] = field(default_factory=dict)


class FilterPipeline:
    """
    Cascading filter pipeline for multi-stage anomaly detection

    Filters are executed in order. Each filter can:
    - Process all logs
    - Add its own anomaly score
    - Mark logs as anomalous

    Example:
        pipeline = FilterPipeline()
        pipeline.add_filter(RFFilter(threshold=0.3))
        pipeline.add_filter(LogBERTFilter(threshold=0.5))

        results = pipeline.run(logs)
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        """
        Initialize filter pipeline

        Args:
            config: Pipeline configuration
        """
        self.config = config or PipelineConfig()
        self.filters: List[BaseFilter] = []
        self.stats = PipelineStats()

    def add_filter(self, filter: BaseFilter) -> 'FilterPipeline':
        """
        Add a filter to the pipeline

        Args:
            filter: Filter instance to add

        Returns:
            Self for method chaining
        """
        self.filters.append(filter)
        return self

    def remove_filter(self, filter_name: str) -> bool:
        """
        Remove a filter by name

        Args:
            filter_name: Name of filter to remove

        Returns:
            True if removed, False if not found
        """
        initial_length = len(self.filters)
        self.filters = [f for f in self.filters if f.filter_name != filter_name]
        return len(self.filters) < initial_length

    def get_filter(self, filter_name: str) -> Optional[BaseFilter]:
        """
        Get a filter by name

        Args:
            filter_name: Name of filter to get

        Returns:
            Filter instance or None if not found
        """
        for filter in self.filters:
            if filter.filter_name == filter_name:
                return filter
        return None

    def run(self, logs: List[Dict[str, Any]]) -> List[FilterResult]:
        """
        Run all enabled filters in the pipeline

        Args:
            logs: List of log entries to process

        Returns:
            List of FilterResult objects from the final stage
        """
        if not logs:
            return []

        start_time = time.time()

        # Filter only enabled filters
        enabled_filters = [f for f in self.filters if f.enabled]

        if not enabled_filters:
            raise RuntimeError("No enabled filters in pipeline")

        # Run filters sequentially
        current_results = None

        for i, filter in enumerate(enabled_filters):
            filter_start = time.time()

            # First filter processes raw logs
            if i == 0:
                current_results = filter.predict(logs)
            else:
                # Subsequent filters process previous results
                # Extract logs from FilterResult objects
                logs_to_process = [r.log for r in current_results]
                current_results = filter.predict(logs_to_process)

            filter_time = (time.time() - filter_start) * 1000

            # Update statistics
            self.stats.filter_stats[filter.filter_name] = {
                "execution_time_ms": filter_time,
                "processed": len(current_results),
                "anomalies": sum(1 for r in current_results if r.is_anomaly)
            }

            # Early stopping: if no anomalies detected, skip remaining filters
            if self.config.early_stopping:
                if not any(r.is_anomaly for r in current_results):
                    break

        # Aggregate scores if configured
        if self.config.aggregate_scores and len(enabled_filters) > 1:
            current_results = self._aggregate_results(current_results)

        # Update global statistics
        self.stats.total_processed += len(logs)
        self.stats.total_anomalies += sum(1 for r in current_results if r.is_anomaly)
        self.stats.execution_time_ms = (time.time() - start_time) * 1000

        return current_results

    def _aggregate_results(self, results: List[FilterResult]) -> List[FilterResult]:
        """
        Aggregate scores from multiple filters

        Args:
            results: Results from the last filter

        Returns:
            Updated results with aggregated scores
        """
        # For now, just return results from last filter
        # Future: could implement score averaging, voting, etc.
        return results

    def get_stats(self) -> Dict[str, Any]:
        """
        Get pipeline statistics

        Returns:
            Dictionary of statistics
        """
        return {
            "total_processed": self.stats.total_processed,
            "total_anomalies": self.stats.total_anomalies,
            "execution_time_ms": self.stats.execution_time_ms,
            "anomaly_rate": (
                self.stats.total_anomalies / self.stats.total_processed
                if self.stats.total_processed > 0 else 0
            ),
            "enabled_filters": [f.filter_name for f in self.filters if f.enabled],
            "filter_stats": self.stats.filter_stats
        }

    def reset_stats(self):
        """Reset pipeline statistics"""
        self.stats = PipelineStats()
        for filter in self.filters:
            if hasattr(filter, 'stats'):
                filter.stats = {
                    "total_processed": 0,
                    "total_anomalies": 0,
                    "total_batches": 0
                }

    def __repr__(self) -> str:
        """String representation of pipeline"""
        enabled = [f.filter_name for f in self.filters if f.enabled]
        disabled = [f.filter_name for f in self.filters if not f.enabled]

        return (
            f"FilterPipeline(\n"
            f"  enabled={enabled},\n"
            f"  disabled={disabled},\n"
            f"  config={self.config}\n"
            f")"
        )
