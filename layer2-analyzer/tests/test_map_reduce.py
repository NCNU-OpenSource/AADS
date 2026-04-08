"""
Tests for Map-Reduce aggregation logic

Tests that deduplicate_and_summarize correctly aggregates data
from multiple containers and calculates severity.
"""
import pytest
from datetime import datetime, timedelta
from aggregator.map_reduce import deduplicate_and_summarize, format_summary_for_prompt


def test_deduplicate_and_summarize_basic():
    """Test basic aggregation with multiple containers"""
    # Setup test data
    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=5)

    cluster_data = {
        'containers': {'nginx-1', 'nginx-2'},
        'anomalies': [
            {
                'container': 'nginx-1',
                'template': 'Connection refused',
                'raw_message': 'Connection refused to backend',
                'anomaly_score': 0.8
            },
            {
                'container': 'nginx-2',
                'template': 'Connection refused',
                'raw_message': 'Connection refused to backend',
                'anomaly_score': 0.85
            },
            {
                'container': 'nginx-1',
                'template': 'Timeout',
                'raw_message': 'Request timeout',
                'anomaly_score': 0.6
            }
        ],
        'start_time': start_time,
        'end_time': end_time,
        'templates': {'Connection refused', 'Timeout'}
    }

    # Run aggregation
    summary = deduplicate_and_summarize(cluster_data)

    # Assertions
    assert len(summary['affected_containers']) == 2
    assert 'nginx-1' in summary['affected_containers']
    assert 'nginx-2' in summary['affected_containers']

    assert summary['total_anomalies'] == 3

    # Check template summary
    assert 'Connection refused' in summary['template_summary']
    conn_refused = summary['template_summary']['Connection refused']
    assert conn_refused['count'] == 2
    assert set(conn_refused['affected_containers']) == {'nginx-1', 'nginx-2'}
    assert conn_refused['max_score'] == 0.85

    assert 'Timeout' in summary['template_summary']
    timeout = summary['template_summary']['Timeout']
    assert timeout['count'] == 1
    assert timeout['affected_containers'] == ['nginx-1']


def test_severity_calculation():
    """Test severity level calculation"""
    start_time = datetime.now()

    # Test critical: high score + many containers
    cluster_data = {
        'containers': {'c1', 'c2', 'c3'},
        'anomalies': [
            {'container': 'c1', 'template': 'Error', 'raw_message': 'err', 'anomaly_score': 0.9},
            {'container': 'c2', 'template': 'Error', 'raw_message': 'err', 'anomaly_score': 0.85},
            {'container': 'c3', 'template': 'Error', 'raw_message': 'err', 'anomaly_score': 0.8},
        ],
        'start_time': start_time,
        'end_time': start_time + timedelta(minutes=5),
        'templates': {'Error'}
    }
    summary = deduplicate_and_summarize(cluster_data)
    assert summary['severity'] == 'critical'

    # Test high: high score
    cluster_data['containers'] = {'c1'}
    cluster_data['anomalies'] = [
        {'container': 'c1', 'template': 'Error', 'raw_message': 'err', 'anomaly_score': 0.75}
    ]
    summary = deduplicate_and_summarize(cluster_data)
    assert summary['severity'] == 'high'

    # Test medium: moderate score
    cluster_data['anomalies'] = [
        {'container': 'c1', 'template': 'Error', 'raw_message': 'err', 'anomaly_score': 0.55}
    ]
    summary = deduplicate_and_summarize(cluster_data)
    assert summary['severity'] == 'medium'

    # Test low: low score
    cluster_data['anomalies'] = [
        {'container': 'c1', 'template': 'Warning', 'raw_message': 'warn', 'anomaly_score': 0.4}
    ]
    summary = deduplicate_and_summarize(cluster_data)
    assert summary['severity'] == 'low'


def test_format_summary_for_prompt():
    """Test prompt formatting"""
    start_time = datetime.now()
    end_time = start_time + timedelta(minutes=5)

    summary = {
        'severity': 'high',
        'affected_containers': ['nginx-1', 'nginx-2'],
        'total_anomalies': 10,
        'time_range': {
            'start': start_time.isoformat(),
            'end': end_time.isoformat(),
            'duration_seconds': 300
        },
        'template_summary': {
            'Connection refused': {
                'count': 8,
                'affected_containers': ['nginx-1', 'nginx-2'],
                'max_score': 0.85,
                'avg_score': 0.8,
                'sample_messages': [
                    {'container': 'nginx-1', 'message': 'Connection refused', 'score': 0.8}
                ]
            }
        }
    }

    prompt = format_summary_for_prompt(summary)

    # Check key sections are present
    assert '## Aggregated Anomaly Summary' in prompt
    assert '**Severity**: high' in prompt
    assert 'nginx-1, nginx-2' in prompt
    assert '**Total Anomalies**: 10' in prompt  # Fixed: includes markdown bold formatting
    assert 'Connection refused' in prompt


def test_empty_cluster():
    """Test handling of empty cluster"""
    cluster_data = {
        'containers': set(),
        'anomalies': [],
        'start_time': datetime.now(),
        'end_time': datetime.now(),
        'templates': set()
    }

    summary = deduplicate_and_summarize(cluster_data)

    assert summary['total_anomalies'] == 0
    assert len(summary['affected_containers']) == 0
    assert len(summary['template_summary']) == 0
    assert summary['severity'] == 'low'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
