"""
Loki HTTP Polling Client
Alternative to WebSocket - polls Loki API periodically
"""
import requests
import asyncio
from datetime import datetime, timedelta
from typing import Callable, Dict, Any


class LokiPollClient:
    """Loki HTTP polling client for log streaming"""

    def __init__(self, loki_url: str, query: str, poll_interval: float = 5.0, tenant_id: str = "raw"):
        """
        Initialize Loki polling client

        Args:
            loki_url: Loki base URL (e.g., http://loki:3100)
            query: LogQL query expression (e.g., '{source="docker"}')
            poll_interval: Polling interval in seconds (default 5.0)
            tenant_id: Loki tenant ID for multi-tenancy (default "raw")
        """
        self.loki_url = loki_url
        self.query = query
        self.poll_interval = poll_interval
        self.tenant_id = tenant_id
        self.last_timestamp = None  # Track last seen timestamp

    async def stream(self, callback: Callable[[Dict], Any]):
        """
        Poll logs from Loki and call callback for each new log entry

        Args:
            callback: Async function to process each log entry
                      Receives dict with: timestamp, labels, message
        """
        print(f"[{datetime.now()}] Starting Loki HTTP Polling Client")
        print(f"  Loki URL: {self.loki_url}")
        print(f"  Query: {self.query}")
        print(f"  Tenant ID: {self.tenant_id}")
        print(f"  Poll Interval: {self.poll_interval}s")

        while True:
            try:
                # Calculate time range (poll last 30 seconds)
                end = datetime.utcnow()
                start = end - timedelta(seconds=30)

                # If we have a last timestamp, use it as start
                if self.last_timestamp:
                    start = datetime.fromtimestamp(int(self.last_timestamp) / 1e9)

                # Query Loki
                params = {
                    "query": self.query,
                    "start": int(start.timestamp() * 1e9),
                    "end": int(end.timestamp() * 1e9),
                    "limit": 1000,
                    "direction": "forward"  # Get oldest first to maintain order
                }

                response = requests.get(
                    f"{self.loki_url}/loki/api/v1/query_range",
                    params=params,
                    headers={"X-Scope-OrgID": self.tenant_id},
                    timeout=10
                )
                response.raise_for_status()

                data = response.json()

                if data["status"] == "success":
                    new_logs = 0

                    for stream in data["data"]["result"]:
                        labels = stream["stream"]

                        for value in stream["values"]:
                            timestamp, log_line = value[0], value[1]

                            # Skip if we've seen this timestamp before
                            if self.last_timestamp and int(timestamp) <= int(self.last_timestamp):
                                continue

                            # Call user callback
                            await callback({
                                "timestamp": timestamp,
                                "labels": labels,
                                "message": log_line
                            })

                            new_logs += 1
                            self.last_timestamp = timestamp

                    if new_logs > 0:
                        print(f"[{datetime.now()}] Fetched {new_logs} new logs")

            except Exception as e:
                print(f"[{datetime.now()}] Error polling Loki: {e}")

            # Wait before next poll
            await asyncio.sleep(self.poll_interval)
