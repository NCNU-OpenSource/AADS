"""
Base Filter Interface for Cascading Filter Pipeline

All log anomaly filters must inherit from BaseFilter and implement required methods.
This enables a pluggable architecture where different filters (RF, LogBERT, Transformer)
can be chained together in a pipeline.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class FilterResult:
    """Result from a filter prediction"""
    log: Dict[str, Any]           # Original log entry
    anomaly_score: float          # Anomaly score (higher = more anomalous)
    is_anomaly: bool              # Whether this log is classified as anomalous
    filter_stage: str             # Which filter produced this result
    metadata: Dict[str, Any]      # Additional filter-specific metadata


class BaseFilter(ABC):
    """
    Abstract base class for all log anomaly filters

    Filters in the cascade:
    - RFFilter: Fast coarse filtering using Random Forest (high recall)
    - LogBERTFilter: Precise classification using BERT (high precision)
    - TransformerFilter: Advanced models (future extension)
    """

    def __init__(self, threshold: float = 0.5, enabled: bool = True):
        """
        Initialize filter

        Args:
            threshold: Anomaly score threshold (0-1, higher = stricter)
            enabled: Whether this filter is enabled in the pipeline
        """
        self._threshold = threshold
        self._enabled = enabled

    @property
    @abstractmethod
    def filter_name(self) -> str:
        """Return the name of this filter (e.g., 'rf', 'logbert')"""
        pass

    @property
    def threshold(self) -> float:
        """Return the anomaly threshold"""
        return self._threshold

    @threshold.setter
    def threshold(self, value: float):
        """Set the anomaly threshold"""
        if not 0 <= value <= 10:  # Allow > 1 for BERT loss scores
            raise ValueError("Threshold must be between 0 and 10")
        self._threshold = value

    @property
    def enabled(self) -> bool:
        """Return whether this filter is enabled"""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool):
        """Set whether this filter is enabled"""
        self._enabled = value

    @abstractmethod
    def predict(self, logs: List[Dict[str, Any]]) -> List[FilterResult]:
        """
        Predict anomaly scores for a batch of logs

        Args:
            logs: List of log entries (must include 'message' field)
                  May include 'template' field if already processed

        Returns:
            List of FilterResult objects with scores and classifications
        """
        pass

    def train(self, labeled_data: List[Dict[str, Any]]) -> None:
        """
        Train the filter model using labeled data

        Args:
            labeled_data: List of logs with 'is_anomaly' labels

        Note:
            Some filters (like pre-trained BERT) may not need training.
            Override this method only if your filter supports training.
        """
        raise NotImplementedError(f"{self.filter_name} does not support training")

    def save_model(self, path: str) -> None:
        """
        Save the trained model to disk

        Args:
            path: Directory path to save model
        """
        raise NotImplementedError(f"{self.filter_name} does not support model saving")

    def load_model(self, path: str) -> None:
        """
        Load a trained model from disk

        Args:
            path: Directory path to load model from
        """
        raise NotImplementedError(f"{self.filter_name} does not support model loading")

    def get_stats(self) -> Dict[str, Any]:
        """
        Get filter statistics (processed count, anomaly count, etc.)

        Returns:
            Dictionary of statistics
        """
        return {
            "filter_name": self.filter_name,
            "threshold": self.threshold,
            "enabled": self.enabled
        }


class PassThroughFilter(BaseFilter):
    """
    A no-op filter that passes all logs through unchanged
    Useful for testing and as a placeholder
    """

    @property
    def filter_name(self) -> str:
        return "passthrough"

    def predict(self, logs: List[Dict[str, Any]]) -> List[FilterResult]:
        """All logs are marked as normal (score = 0)"""
        return [
            FilterResult(
                log=log,
                anomaly_score=0.0,
                is_anomaly=False,
                filter_stage=self.filter_name,
                metadata={}
            )
            for log in logs
        ]
