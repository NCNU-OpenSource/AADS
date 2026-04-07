"""
Loki HTTP API Client
Query logs from Loki for anomaly detection
"""
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Any


class LokiClient:
    def __init__(self, base_url: str = "http://loki:3100"):
        self.base_url = base_url
        self.query_endpoint = f"{base_url}/loki/api/v1/query_range"

    def query_logs(
        self,
        query: str,
        start: datetime = None,
        end: datetime = None,
        limit: int = 5000
    ) -> List[Dict[str, Any]]:
        """
        Query logs from Loki using LogQL

        Args:
            query: LogQL query string (e.g., '{source="docker"}')
            start: Start time (default: 1 hour ago)
            end: End time (default: now)
            limit: Maximum number of log entries

        Returns:
            List of log entries with timestamps and labels
        """
        if end is None:
            end = datetime.utcnow()
        if start is None:
            start = end - timedelta(hours=1)

        params = {
            "query": query,
            "start": int(start.timestamp() * 1e9),  # nanoseconds
            "end": int(end.timestamp() * 1e9),
            "limit": limit,
            "direction": "backward"
        }

        response = requests.get(self.query_endpoint, params=params)
        response.raise_for_status()

        data = response.json()
        logs = []

        if data["status"] == "success":
            for stream in data["data"]["result"]:
                labels = stream["stream"]
                for value in stream["values"]:
                    timestamp, log_line = value
                    logs.append({
                        "timestamp": timestamp,
                        "labels": labels,
                        "message": log_line
                    })

        return logs
