"""
Deterministic pattern filter for lab-safe anomaly detection.

This is intentionally simple and high recall. It guarantees that known broken
Ubuntu/nginx lab states are surfaced even when LogBERT confidence drifts.
"""
import re
from typing import Any, Dict, List

from filters.base import BaseFilter, FilterResult


DEFAULT_PATTERNS = [
    r"\bfailed to start\b",
    r"\bnginx.*config.*failed\b",
    r"\bnginx: \[emerg\]",
    r"\bconnection refused\b",
    r"\boutofmemory|out of memory\b",
    r"\bcritical\b",
    r"\bfatal\b",
    r"\bpanic\b",
    r"\b(error|failed|failure)\b",
]


class PatternFilter(BaseFilter):
    """Regex based deterministic anomaly filter."""

    def __init__(self, patterns: List[str] | None = None, enabled: bool = True):
        super().__init__(threshold=1.0, enabled=enabled)
        self.patterns = patterns or DEFAULT_PATTERNS
        self._compiled = [re.compile(pattern, re.IGNORECASE) for pattern in self.patterns]

    @property
    def filter_name(self) -> str:
        return "pattern"

    def predict(self, logs: List[Dict[str, Any]]) -> List[FilterResult]:
        results: List[FilterResult] = []
        for log in logs:
            message = log.get("message") or log.get("raw_message") or ""
            matched = [pattern.pattern for pattern in self._compiled if pattern.search(message)]
            is_anomaly = bool(matched)
            results.append(
                FilterResult(
                    log=log,
                    anomaly_score=1.0 if is_anomaly else 0.0,
                    is_anomaly=is_anomaly,
                    filter_stage=self.filter_name,
                    metadata={"matched_patterns": matched},
                )
            )
        return results
