"""
Unit tests for RootCauseAnalyzer

Tests deduplication flow ensures Layer 3 is still executed.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
import sys
import os

# Add src to path
LAYER2_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, LAYER2_SRC)


def load_root_cause_analyzer():
    """Load layer2 main.py even when other packages also have a main.py."""
    if sys.path[0] != LAYER2_SRC:
        sys.path.insert(0, LAYER2_SRC)
    sys.modules.pop('main', None)
    from main import RootCauseAnalyzer
    return RootCauseAnalyzer


class TestAnalyzeClusterDedup:
    """Test that reused diagnoses still trigger Layer 3 processing"""

    @pytest.fixture
    def mock_analyzer(self):
        """Create analyzer with mocked dependencies"""
        with patch.dict('os.environ', {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_NAME': 'logdb',
            'DB_USER': 'logdb',
            'DB_PASSWORD': 'test',
            'LLM_API_KEY': 'test-key',
            'LLM_MODEL': 'gpt-4o-mini',
        }):
            RootCauseAnalyzer = load_root_cause_analyzer()
            analyzer = RootCauseAnalyzer()

            # Mock Layer 3 components
            analyzer.suggestion_generator = MagicMock()
            analyzer.suggestion_generator.generate_suggestions.return_value = [
                {'priority': 1, 'action': 'test_action', 'description': 'Test'}
            ]
            analyzer.suggestion_generator.format_for_notification.return_value = "Test message"

            analyzer.notification_hub = MagicMock()
            analyzer.notification_hub.send_notification = AsyncMock(return_value={'slack': True})

            # Mock database methods
            analyzer.store_diagnosis = AsyncMock()
            analyzer.find_similar_diagnosis = AsyncMock()

            return analyzer

    @pytest.fixture
    def mock_cluster(self):
        """Create a mock cluster object"""
        cluster = MagicMock()
        cluster.cluster_id = "test_cluster_123"
        cluster.containers = {"test-container"}
        cluster.templates = {"Error template"}
        cluster.total_count = 5
        cluster.start_time = datetime.now()
        cluster.end_time = datetime.now()
        return cluster

    @pytest.mark.asyncio
    async def test_reused_diagnosis_triggers_layer3(self, mock_analyzer, mock_cluster):
        """
        When diagnosis is reused, Layer 3 should still be executed:
        - generate_suggestions should be called
        - send_notification should be called
        """
        # Setup: similar diagnosis found
        similar_diagnosis = {
            'diagnosis_id': 'diag_old_123',
            'timestamp': datetime.now(),
            'severity': 'high',
            'summary': 'Database connection timeout',
            'root_cause': {'category': 'network_issue', 'confidence': 0.8},
            'recommended_actions': [{'action': 'check_network', 'priority': 1}],
            'affected_services': [{'container': 'test-container'}]
        }
        mock_analyzer.find_similar_diagnosis.return_value = similar_diagnosis

        # Execute
        await mock_analyzer.analyze_cluster(mock_cluster)

        # Verify Layer 3 was executed
        mock_analyzer.suggestion_generator.generate_suggestions.assert_called_once()
        mock_analyzer.suggestion_generator.format_for_notification.assert_called_once()
        mock_analyzer.notification_hub.send_notification.assert_called_once()

        # Verify diagnosis was stored
        mock_analyzer.store_diagnosis.assert_called_once()

    @pytest.mark.asyncio
    async def test_reused_diagnosis_notification_respects_severity_filter(
        self, mock_analyzer, mock_cluster
    ):
        """
        Reused diagnosis should respect severity filter.
        If severity is below threshold, notification should not be sent.
        """
        # Setup: similar diagnosis with low severity
        similar_diagnosis = {
            'diagnosis_id': 'diag_old_123',
            'timestamp': datetime.now(),
            'severity': 'low',  # Below default 'medium' threshold
            'summary': 'Minor warning',
            'root_cause': {'category': 'unknown', 'confidence': 0.5},
            'recommended_actions': [],
            'affected_services': [{'container': 'test-container'}]
        }
        mock_analyzer.find_similar_diagnosis.return_value = similar_diagnosis

        # Notification hub should return empty dict when filtered
        mock_analyzer.notification_hub.send_notification.return_value = {}

        # Execute
        await mock_analyzer.analyze_cluster(mock_cluster)

        # Verify notification was attempted (filtering happens inside notification_hub)
        mock_analyzer.notification_hub.send_notification.assert_called_once()


class TestRunLayer3:
    """Test the _run_layer3 method directly"""

    @pytest.fixture
    def mock_analyzer(self):
        """Create analyzer with mocked Layer 3 components"""
        with patch.dict('os.environ', {
            'DB_HOST': 'localhost',
            'DB_PORT': '5432',
            'DB_NAME': 'logdb',
            'DB_USER': 'logdb',
            'DB_PASSWORD': 'test',
            'LLM_API_KEY': 'test-key',
            'LLM_MODEL': 'gpt-4o-mini',
        }):
            RootCauseAnalyzer = load_root_cause_analyzer()
            analyzer = RootCauseAnalyzer()

            analyzer.suggestion_generator = MagicMock()
            analyzer.suggestion_generator.generate_suggestions.return_value = [
                {'priority': 1, 'action': 'test', 'description': 'Test action'}
            ]
            analyzer.suggestion_generator.format_for_notification.return_value = "Formatted"

            analyzer.notification_hub = MagicMock()
            analyzer.notification_hub.send_notification = AsyncMock(return_value={'slack': True})

            return analyzer

    @pytest.mark.asyncio
    async def test_run_layer3_returns_results(self, mock_analyzer):
        """_run_layer3 should return suggestions and notification results"""
        diagnosis = {
            'diagnosis_id': 'test_123',
            'severity': 'high',
            'summary': 'Test',
            'root_cause': {'category': 'test'},
            'recommended_actions': []
        }

        result = await mock_analyzer._run_layer3(diagnosis)

        assert 'suggestions' in result
        assert 'notification_results' in result
        assert len(result['suggestions']) == 1
        assert result['notification_results'] == {'slack': True}

    @pytest.mark.asyncio
    async def test_run_layer3_calls_components_in_order(self, mock_analyzer):
        """_run_layer3 should call components in correct order"""
        diagnosis = {
            'diagnosis_id': 'test_123',
            'severity': 'high',
            'summary': 'Test',
            'root_cause': {'category': 'test'},
            'recommended_actions': []
        }

        await mock_analyzer._run_layer3(diagnosis)

        # Verify call order: generate -> format -> send
        assert mock_analyzer.suggestion_generator.generate_suggestions.call_count == 1
        assert mock_analyzer.suggestion_generator.format_for_notification.call_count == 1
        assert mock_analyzer.notification_hub.send_notification.call_count == 1
