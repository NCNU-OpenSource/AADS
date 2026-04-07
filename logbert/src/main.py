"""
LogBERT Log Anomaly Detection Pipeline
Real-time anomaly detection using Loki WebSocket streaming
"""
import json
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from loki_poll_client import LokiPollClient
from log_buffer import LogBuffer
from log_processor import LogProcessor
from anomaly_detector import AnomalyDetector

# Configuration
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "0.5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
MAX_WAIT_SECONDS = float(os.getenv("MAX_WAIT_SECONDS", "2.0"))
OUTPUT_DIR = Path("/app/output")


def push_anomalies_to_loki(anomalies: List[Dict], loki_url: str, tenant_id: str = "anomalies"):
    """
    Push anomalies back to Loki with special labels for Grafana visualization

    Args:
        anomalies: List of anomaly dictionaries
        loki_url: Loki base URL
        tenant_id: Loki tenant ID (default "anomalies" for isolation)
    """
    if not anomalies:
        return

    import requests

    # Group anomalies by container for better organization
    streams = []
    for anomaly in anomalies:
        timestamp_ns = anomaly.get("timestamp", str(int(datetime.utcnow().timestamp() * 1e9)))
        container = anomaly.get("labels", {}).get("container", "unknown")

        # Create a stream for each anomaly
        log_entry = {
            "message": anomaly["message"],
            "score": anomaly["anomaly_score"],
            "template": anomaly.get("template", ""),
            "is_anomaly": anomaly["is_anomaly"]
        }

        streams.append({
            "stream": {
                "source": "logbert",
                "type": "anomaly",
                "container": container,
                "severity": "high" if anomaly["anomaly_score"] > 0.7 else "medium"
            },
            "values": [[timestamp_ns, json.dumps(log_entry)]]
        })

    # Push in batches of 100 to avoid payload size issues
    batch_size = 100
    for i in range(0, len(streams), batch_size):
        batch = streams[i:i + batch_size]
        try:
            response = requests.post(
                f"{loki_url}/loki/api/v1/push",
                json={"streams": batch},
                headers={
                    "Content-Type": "application/json",
                    "X-Scope-OrgID": tenant_id  # Multi-tenancy: use separate tenant for anomalies
                },
                timeout=10
            )
            response.raise_for_status()
        except Exception as e:
            print(f"  Warning: Failed to push batch {i//batch_size + 1} to Loki: {e}")


async def main():
    print(f"[{datetime.now()}] Starting LogBERT Real-time Anomaly Detection Service")
    print(f"  - Loki URL: {LOKI_URL}")
    print(f"  - Batch Size: {BATCH_SIZE}")
    print(f"  - Max Wait: {MAX_WAIT_SECONDS}s")
    print(f"  - Anomaly Threshold: {ANOMALY_THRESHOLD}")
    print(f"  - Mode: HTTP Polling (5s interval)")

    # Initialize components
    processor = LogProcessor()
    detector = AnomalyDetector(threshold=ANOMALY_THRESHOLD)

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Statistics
    stats = {
        "total_processed": 0,
        "total_anomalies": 0,
        "last_report": datetime.now()
    }

    # Define flush callback for buffer
    async def on_logs_flush(logs: List[Dict[str, Any]]):
        """Process a batch of logs"""
        try:
            now = datetime.now()
            print(f"\n[{now}] Processing {len(logs)} logs...")

            # Process logs
            processed = processor.process_logs(logs)
            print(f"  Processed {len(processed)} logs")

            # Detect anomalies
            results = detector.detect_anomalies(processed)
            print(f"  Analyzed {len(results)} results")

            # Filter anomalies
            anomalies = [r for r in results if r["is_anomaly"]]

            # Update statistics
            stats["total_processed"] += len(logs)
            stats["total_anomalies"] += len(anomalies)

            if anomalies:
                print(f"  ⚠️  Detected {len(anomalies)} anomalous logs!")

                # Save anomalies to file
                output_file = OUTPUT_DIR / f"anomalies_{now.strftime('%Y%m%d_%H%M%S')}.json"
                with open(output_file, "w") as f:
                    json.dump(anomalies, f, indent=2, default=str)
                print(f"  Saved to: {output_file}")

                # Also append to consolidated file
                consolidated = OUTPUT_DIR / "anomalies.json"
                existing = []
                if consolidated.exists():
                    with open(consolidated) as f:
                        existing = json.load(f)
                existing.extend(anomalies)
                # Keep only last 10000 anomalies
                existing = existing[-10000:]
                with open(consolidated, "w") as f:
                    json.dump(existing, f, indent=2, default=str)

                # Push anomalies back to Loki for Grafana visualization
                print(f"  Pushing {len(anomalies)} anomalies to Loki...")
                push_anomalies_to_loki(anomalies, LOKI_URL)
                print(f"  ✓ Anomalies pushed to Loki")
            else:
                print("  ✓ No anomalies detected in this batch")

            # Print periodic statistics
            if (now - stats["last_report"]).seconds >= 60:
                print(f"\n[Statistics] Total processed: {stats['total_processed']}, "
                      f"Total anomalies: {stats['total_anomalies']}")
                stats["last_report"] = now

        except Exception as e:
            print(f"  Error processing logs: {e}")
            import traceback
            traceback.print_exc()

    # Create log buffer
    buffer = LogBuffer(max_size=BATCH_SIZE, max_wait=MAX_WAIT_SECONDS)
    buffer.on_flush = on_logs_flush
    buffer.start_timer()

    # Create Loki polling client (read from "raw" tenant)
    # Exclude LogBERT's own logs to avoid self-referential anomaly detection
    client = LokiPollClient(
        loki_url=LOKI_URL,
        query='{source="docker", container!="logbert"}',  # Exclude logbert container
        poll_interval=5.0,  # Poll every 5 seconds
        tenant_id="raw"  # Multi-tenancy: read only from raw logs tenant
    )

    # Start streaming (this runs forever)
    try:
        await client.stream(buffer.add)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        await buffer.stop()
        print("Goodbye!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
