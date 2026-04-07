"""
Suggestion Generator - Generate actionable remediation suggestions

Based on diagnosis reports, generates concrete suggestions.
"""
import logging
from typing import List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class SuggestionGenerator:
    """
    Generate remediation suggestions from diagnosis

    Features:
    - Rule-based suggestions for common issues
    - Priority ranking
    - Actionable instructions
    - Safety checks (read-only suggestions only)
    """

    def __init__(self, auto_remediation_enabled: bool = False):
        """
        Initialize suggestion generator

        Args:
            auto_remediation_enabled: Enable automatic remediation (default: False for safety)
        """
        self.auto_remediation_enabled = auto_remediation_enabled

        # Rule-based suggestion templates
        self.suggestion_rules = {
            "dependency_failure": [
                {
                    "priority": 1,
                    "action": "check_network_connectivity",
                    "description": "檢查網路連線是否正常",
                    "command": "ping -c 3 8.8.8.8"
                },
                {
                    "priority": 2,
                    "action": "verify_dns",
                    "description": "驗證 DNS 解析是否正常",
                    "command": "nslookup google.com"
                },
                {
                    "priority": 3,
                    "action": "check_external_service",
                    "description": "檢查外部服務狀態"
                }
            ],
            "resource_exhaustion": [
                {
                    "priority": 1,
                    "action": "check_resource_usage",
                    "description": "檢查資源使用情況",
                    "command": "docker stats --no-stream"
                },
                {
                    "priority": 2,
                    "action": "increase_resource_limits",
                    "description": "考慮增加容器資源限制（CPU/Memory）"
                },
                {
                    "priority": 3,
                    "action": "scale_horizontally",
                    "description": "考慮水平擴展服務"
                }
            ],
            "configuration_error": [
                {
                    "priority": 1,
                    "action": "review_configuration",
                    "description": "檢查配置檔案",
                    "command": "docker inspect <container_name>"
                },
                {
                    "priority": 2,
                    "action": "check_environment_variables",
                    "description": "驗證環境變數設定"
                },
                {
                    "priority": 3,
                    "action": "compare_with_working_config",
                    "description": "與正常工作的配置比對"
                }
            ],
            "network_issue": [
                {
                    "priority": 1,
                    "action": "check_network_connectivity",
                    "description": "測試網路連線",
                    "command": "docker network inspect <network_name>"
                },
                {
                    "priority": 2,
                    "action": "check_firewall_rules",
                    "description": "檢查防火牆規則"
                },
                {
                    "priority": 3,
                    "action": "verify_dns",
                    "description": "驗證 DNS 設定"
                }
            ],
            "unknown": [
                {
                    "priority": 1,
                    "action": "manual_investigation",
                    "description": "需要人工檢查日誌和系統狀態"
                },
                {
                    "priority": 2,
                    "action": "check_recent_changes",
                    "description": "檢查最近的系統變更"
                }
            ]
        }

        self.stats = {
            "total_suggestions_generated": 0,
            "by_category": {}
        }

    def generate_suggestions(self, diagnosis: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Generate remediation suggestions based on diagnosis

        Args:
            diagnosis: Diagnosis dictionary from LLMReasoner

        Returns:
            List of suggestion dictionaries
        """
        root_cause = diagnosis.get('root_cause', {})
        category = root_cause.get('category', 'unknown')
        confidence = root_cause.get('confidence', 0.0)

        logger.info(f"Generating suggestions for category: {category} (confidence: {confidence})")

        # Get base suggestions from rules
        base_suggestions = self.suggestion_rules.get(category, self.suggestion_rules['unknown'])

        # Enrich with diagnosis-specific information
        suggestions = []
        for template in base_suggestions:
            suggestion = {
                **template,
                "diagnosis_id": diagnosis.get('diagnosis_id'),
                "timestamp": datetime.now(),
                "confidence": confidence,
                "safe_to_automate": self._is_safe_to_automate(template['action']),
                "requires_approval": not self._is_safe_to_automate(template['action'])
            }

            # Add container-specific information if available
            affected_services = diagnosis.get('affected_services', [])
            if affected_services and 'command' in template:
                container = affected_services[0].get('container', '<container_name>')
                suggestion['command'] = template['command'].replace('<container_name>', container)

            suggestions.append(suggestion)

        # Add LLM-generated actions from diagnosis
        llm_actions = diagnosis.get('recommended_actions', [])
        for action in llm_actions:
            suggestions.append({
                "priority": action.get('priority', 99),
                "action": action.get('action', 'custom_action'),
                "description": action.get('description', ''),
                "diagnosis_id": diagnosis.get('diagnosis_id'),
                "timestamp": datetime.now(),
                "confidence": confidence,
                "safe_to_automate": False,  # LLM suggestions require review
                "requires_approval": True,
                "source": "llm"
            })

        # Sort by priority
        suggestions.sort(key=lambda x: x['priority'])

        # Update statistics
        self.stats["total_suggestions_generated"] += len(suggestions)
        self.stats["by_category"][category] = self.stats["by_category"].get(category, 0) + 1

        logger.info(f"Generated {len(suggestions)} suggestions for {diagnosis.get('diagnosis_id')}")

        return suggestions

    def _is_safe_to_automate(self, action: str) -> bool:
        """
        Determine if an action is safe to automate

        Args:
            action: Action name

        Returns:
            True if safe to automate without approval
        """
        # Only read-only/monitoring actions are safe
        safe_actions = {
            'check_resource_usage',
            'check_network_connectivity',
            'verify_dns',
            'review_configuration',
            'check_environment_variables',
            'manual_investigation',
            'check_recent_changes',
            'check_external_service',
            'check_firewall_rules'
        }

        return action in safe_actions

    def format_for_notification(
        self,
        diagnosis: Dict[str, Any],
        suggestions: List[Dict[str, Any]]
    ) -> str:
        """
        Format diagnosis and suggestions for notification

        Args:
            diagnosis: Diagnosis dictionary
            suggestions: List of suggestions

        Returns:
            Formatted message string (Markdown)
        """
        lines = []

        # Header
        severity_emoji = {
            'low': '🟢',
            'medium': '🟡',
            'high': '🟠',
            'critical': '🔴'
        }
        severity = diagnosis.get('severity', 'medium')
        emoji = severity_emoji.get(severity, '⚪')

        lines.append(f"{emoji} **AI Debug Alert - {severity.upper()}**")
        lines.append("")

        # Summary
        lines.append(f"**Summary:** {diagnosis.get('summary', 'No summary available')}")
        lines.append("")

        # Root Cause
        root_cause = diagnosis.get('root_cause', {})
        lines.append("**Root Cause:**")
        lines.append(f"- Category: `{root_cause.get('category', 'unknown')}`")
        lines.append(f"- Confidence: {root_cause.get('confidence', 0)*100:.1f}%")
        lines.append(f"- Description: {root_cause.get('description', 'No description')}")
        lines.append("")

        # Affected Services
        affected = diagnosis.get('affected_services', [])
        if affected:
            lines.append("**Affected Services:**")
            for service in affected[:3]:  # Limit to 3
                container = service.get('container', 'unknown')
                count = service.get('anomaly_count', 0)
                lines.append(f"- `{container}` ({count} anomalies)")
            lines.append("")

        # Suggested Actions
        lines.append("**Recommended Actions:**")
        for i, suggestion in enumerate(suggestions[:5], 1):  # Limit to top 5
            desc = suggestion.get('description', '')
            lines.append(f"{i}. {desc}")
            if suggestion.get('command'):
                lines.append(f"   ```\n   {suggestion['command']}\n   ```")
        lines.append("")

        # Metadata
        lines.append("---")
        lines.append(f"Diagnosis ID: `{diagnosis.get('diagnosis_id')}`")
        lines.append(f"Timestamp: {diagnosis.get('timestamp')}")

        return "\n".join(lines)

    def get_stats(self) -> Dict[str, Any]:
        """Get generator statistics"""
        return dict(self.stats)
