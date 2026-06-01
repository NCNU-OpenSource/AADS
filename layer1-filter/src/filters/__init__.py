"""
Layer 1 Filters Package

Available filters:
- BaseFilter: Abstract base class
- PassThroughFilter: No-op filter for testing
- RFFilter: Random Forest filter (reserved, not implemented)
- LogBERTFilter: BERT-based anomaly detection
"""

from .base import BaseFilter, FilterResult, PassThroughFilter
from .rf_filter import RFFilter
from .pattern_filter import PatternFilter


def __getattr__(name):
    if name == "LogBERTFilter":
        from .logbert_filter import LogBERTFilter

        return LogBERTFilter
    raise AttributeError(name)

__all__ = [
    'BaseFilter',
    'FilterResult',
    'PassThroughFilter',
    'RFFilter',
    'LogBERTFilter',
    'PatternFilter'
]
