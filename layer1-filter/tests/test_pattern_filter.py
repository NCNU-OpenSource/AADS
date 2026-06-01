import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from filters.pattern_filter import PatternFilter


def test_pattern_filter_detects_nginx_config_failure():
    filt = PatternFilter()
    results = filt.predict([
        {"message": "nginx: [emerg] invalid number of arguments in listen directive"}
    ])
    assert results[0].is_anomaly is True
    assert results[0].filter_stage == "pattern"
    assert results[0].metadata["matched_patterns"]


def test_pattern_filter_ignores_normal_message():
    filt = PatternFilter()
    results = filt.predict([{"message": "INFO nginx worker process started"}])
    assert results[0].is_anomaly is False
