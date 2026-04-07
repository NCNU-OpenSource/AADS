"""
Log Preprocessor for LogBERT
Transform raw logs into structured sequences for BERT input
"""
import re
from typing import List, Dict, Any
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


class LogProcessor:
    def __init__(self):
        # Drain3 for log parsing (extract templates)
        config = TemplateMinerConfig()
        config.drain_depth = 4
        config.drain_sim_th = 0.4
        self.template_miner = TemplateMiner(config=config)

        # Common log patterns to normalize
        self.patterns = [
            (r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?', '<TIMESTAMP>'),
            (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<IP>'),
            (r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<UUID>'),
            (r'\b[0-9a-f]{12,64}\b', '<HEX>'),
            (r'\b\d+\b', '<NUM>'),
        ]

    def normalize(self, log_line: str) -> str:
        """Normalize log line by replacing variables with tokens"""
        normalized = log_line
        for pattern, replacement in self.patterns:
            normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
        return normalized

    def extract_template(self, log_line: str) -> str:
        """Extract log template using Drain3"""
        result = self.template_miner.add_log_message(log_line)
        # Drain3 returns a dict with 'template_mined' key
        if isinstance(result, dict):
            return result.get('template_mined', log_line)
        # Fallback for older Drain3 versions
        return getattr(result, 'template_mined', log_line) if result else log_line

    def process_logs(self, logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process raw logs into structured format for LogBERT"""
        processed = []
        for log in logs:
            message = log["message"]
            normalized = self.normalize(message)
            template = self.extract_template(normalized)

            processed.append({
                **log,
                "normalized": normalized,
                "template": template
            })

        return processed
