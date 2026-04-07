"""
Notification Hub - Send notifications to multiple channels

Supports Slack, Webhook, and future channels.
"""
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime
import aiohttp

logger = logging.getLogger(__name__)


class NotificationHub:
    """
    Multi-channel notification system

    Channels:
    - Slack (via Webhook)
    - Generic Webhook
    - Email (future)
    """

    def __init__(
        self,
        slack_webhook_url: Optional[str] = None,
        custom_webhook_url: Optional[str] = None,
        severity_filter: str = "medium"
    ):
        """
        Initialize notification hub

        Args:
            slack_webhook_url: Slack incoming webhook URL
            custom_webhook_url: Custom webhook URL
            severity_filter: Minimum severity to send notifications (low|medium|high|critical)
        """
        self.slack_webhook_url = slack_webhook_url
        self.custom_webhook_url = custom_webhook_url
        self.severity_filter = severity_filter

        # Severity levels
        self.severity_levels = {
            'low': 0,
            'medium': 1,
            'high': 2,
            'critical': 3
        }

        self.stats = {
            "total_sent": 0,
            "total_errors": 0,
            "by_channel": {
                "slack": 0,
                "webhook": 0
            }
        }

        logger.info(
            f"Notification hub initialized "
            f"(Slack: {bool(slack_webhook_url)}, "
            f"Webhook: {bool(custom_webhook_url)}, "
            f"Filter: {severity_filter})"
        )

    async def send_notification(
        self,
        diagnosis: Dict[str, Any],
        suggestions: List[Dict[str, Any]],
        formatted_message: str
    ) -> Dict[str, bool]:
        """
        Send notification to all configured channels

        Args:
            diagnosis: Diagnosis dictionary
            suggestions: List of suggestions
            formatted_message: Pre-formatted message (Markdown)

        Returns:
            Dictionary of channel: success status
        """
        severity = diagnosis.get('severity', 'medium')

        # Check severity filter
        if not self._should_notify(severity):
            logger.info(f"Skipping notification: severity {severity} below threshold {self.severity_filter}")
            return {}

        results = {}

        # Send to Slack
        if self.slack_webhook_url:
            try:
                success = await self._send_slack(formatted_message, diagnosis, suggestions)
                results['slack'] = success
                if success:
                    self.stats["by_channel"]["slack"] += 1
                else:
                    self.stats["total_errors"] += 1
            except Exception as e:
                logger.error(f"Error sending Slack notification: {e}")
                results['slack'] = False
                self.stats["total_errors"] += 1

        # Send to custom webhook
        if self.custom_webhook_url:
            try:
                success = await self._send_webhook(diagnosis, suggestions)
                results['webhook'] = success
                if success:
                    self.stats["by_channel"]["webhook"] += 1
                else:
                    self.stats["total_errors"] += 1
            except Exception as e:
                logger.error(f"Error sending webhook notification: {e}")
                results['webhook'] = False
                self.stats["total_errors"] += 1

        if any(results.values()):
            self.stats["total_sent"] += 1

        return results

    async def _send_slack(
        self,
        formatted_message: str,
        diagnosis: Dict[str, Any],
        suggestions: List[Dict[str, Any]]
    ) -> bool:
        """
        Send notification to Slack

        Args:
            formatted_message: Formatted message (Markdown)
            diagnosis: Diagnosis dictionary
            suggestions: List of suggestions

        Returns:
            True if successful
        """
        # Convert to Slack blocks format
        severity = diagnosis.get('severity', 'medium')
        severity_colors = {
            'low': '#36a64f',      # Green
            'medium': '#ff9900',   # Orange
            'high': '#ff6600',     # Dark Orange
            'critical': '#ff0000'  # Red
        }

        payload = {
            "attachments": [
                {
                    "color": severity_colors.get(severity, '#808080'),
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"🤖 AI Debug Alert - {severity.upper()}"
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": formatted_message
                            }
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"Generated by AI Auto-Debug System | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.slack_webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    logger.info("Slack notification sent successfully")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Slack notification failed: {response.status} - {error_text}")
                    return False

    async def _send_webhook(
        self,
        diagnosis: Dict[str, Any],
        suggestions: List[Dict[str, Any]]
    ) -> bool:
        """
        Send notification to custom webhook

        Args:
            diagnosis: Diagnosis dictionary
            suggestions: List of suggestions

        Returns:
            True if successful
        """
        payload = {
            "event_type": "diagnosis_complete",
            "timestamp": datetime.now().isoformat(),
            "diagnosis": {
                "id": diagnosis.get('diagnosis_id'),
                "severity": diagnosis.get('severity'),
                "summary": diagnosis.get('summary'),
                "root_cause": diagnosis.get('root_cause'),
                "affected_services": diagnosis.get('affected_services')
            },
            "suggestions": [
                {
                    "priority": s.get('priority'),
                    "action": s.get('action'),
                    "description": s.get('description'),
                    "safe_to_automate": s.get('safe_to_automate', False)
                }
                for s in suggestions[:10]  # Limit to 10
            ]
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.custom_webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status in (200, 201, 202, 204):
                    logger.info("Webhook notification sent successfully")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Webhook notification failed: {response.status} - {error_text}")
                    return False

    def _should_notify(self, severity: str) -> bool:
        """
        Check if severity meets notification threshold

        Args:
            severity: Severity level

        Returns:
            True if should notify
        """
        severity_level = self.severity_levels.get(severity, 0)
        threshold_level = self.severity_levels.get(self.severity_filter, 0)

        return severity_level >= threshold_level

    def get_stats(self) -> Dict[str, Any]:
        """Get notification statistics"""
        return dict(self.stats)
