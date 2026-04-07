"""
Loki WebSocket Tail Client
Real-time log streaming from Loki using WebSocket
"""
import asyncio
import websockets
import json
import urllib.parse
from typing import Callable, Dict, Any
from datetime import datetime


class LokiTailClient:
    """Loki WebSocket tail client for real-time log streaming"""

    def __init__(self, loki_url: str, query: str, limit: int = 100, delay_for: int = 0, tenant_id: str = "raw"):
        """
        Initialize Loki tail client

        Args:
            loki_url: Loki base URL (e.g., http://loki:3100)
            query: LogQL query expression (e.g., '{source="docker"}')
            limit: Maximum entries per message (default 100)
            delay_for: Delay in seconds for slow loggers (0-5, default 0)
            tenant_id: Loki tenant ID for multi-tenancy (default "raw")
        """
        # Convert http:// to ws:// for WebSocket
        ws_url = loki_url.replace("http://", "ws://").replace("https://", "wss://")

        # Build WebSocket URL with query parameters
        params = {
            "query": query,
            "limit": str(limit),
            "delay_for": str(delay_for)
        }
        query_string = urllib.parse.urlencode(params)
        self.url = f"{ws_url}/loki/api/v1/tail?{query_string}"

        # Store tenant ID for multi-tenancy support
        self.tenant_id = tenant_id

        self.reconnect_delay = 5  # seconds
        self.max_reconnect_delay = 60  # max backoff

    async def stream(self, callback: Callable[[Dict], Any]):
        """
        Stream logs from Loki and call callback for each log entry

        Args:
            callback: Async function to process each log entry
                      Receives dict with: timestamp, labels, message
        """
        reconnect_delay = self.reconnect_delay

        while True:
            try:
                print(f"[{datetime.now()}] Connecting to Loki WebSocket...")
                print(f"  URL: {self.url}")

                async with websockets.connect(
                    self.url,
                    extra_headers={"X-Scope-OrgID": self.tenant_id},  # Multi-tenancy support
                    ping_interval=20,  # Keep connection alive
                    ping_timeout=10
                ) as ws:
                    print(f"[{datetime.now()}] ✓ Connected to Loki tail")
                    reconnect_delay = self.reconnect_delay  # Reset backoff

                    async for message in ws:
                        try:
                            data = json.loads(message)

                            # Process each stream in the message
                            for stream in data.get("streams", []):
                                labels = stream.get("stream", {})

                                # Process each log entry in the stream
                                for value in stream.get("values", []):
                                    if len(value) >= 2:
                                        timestamp, log_line = value[0], value[1]

                                        # Call user callback
                                        await callback({
                                            "timestamp": timestamp,
                                            "labels": labels,
                                            "message": log_line
                                        })

                        except json.JSONDecodeError as e:
                            print(f"  Warning: Failed to parse message: {e}")
                            continue
                        except Exception as e:
                            print(f"  Warning: Error processing message: {e}")
                            continue

            except websockets.exceptions.ConnectionClosed as e:
                print(f"[{datetime.now()}] WebSocket connection closed: {e}")

            except Exception as e:
                print(f"[{datetime.now()}] WebSocket error: {e}")

            # Exponential backoff for reconnection
            print(f"  Reconnecting in {reconnect_delay} seconds...")
            await asyncio.sleep(reconnect_delay)

            # Increase delay for next attempt (up to max)
            reconnect_delay = min(reconnect_delay * 2, self.max_reconnect_delay)
