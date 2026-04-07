"""
Metrics Correlator - Correlate Prometheus metrics with anomalies

Queries Prometheus for metrics around anomaly time and correlates them.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import aiohttp

logger = logging.getLogger(__name__)


class MetricsCorrelator:
    """
    Correlate Prometheus metrics with anomalies

    Queries:
    - Container CPU/Memory usage
    - GPU utilization and temperature
    - Network I/O
    - Disk I/O
    """

    def __init__(
        self,
        prometheus_url: str = "http://prometheus:9090",
        timeout: int = 30
    ):
        """
        Initialize metrics correlator

        Args:
            prometheus_url: Prometheus server URL
            timeout: Request timeout in seconds
        """
        self.prometheus_url = prometheus_url.rstrip('/')
        self.timeout = timeout

        self.stats = {
            "total_queries": 0,
            "total_errors": 0
        }

    async def query_prometheus(
        self,
        query: str,
        time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Execute a PromQL query

        Args:
            query: PromQL query string
            time: Timestamp for instant query (defaults to now)

        Returns:
            Query result dictionary
        """
        url = f"{self.prometheus_url}/api/v1/query"
        params = {"query": query}

        if time:
            params["time"] = time.timestamp()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Prometheus query failed: {response.status} - {error_text}")
                        self.stats["total_errors"] += 1
                        return {}

                    data = await response.json()
                    self.stats["total_queries"] += 1
                    return data.get('data', {})

        except Exception as e:
            logger.error(f"Error querying Prometheus: {e}")
            self.stats["total_errors"] += 1
            return {}

    async def query_range(
        self,
        query: str,
        start: datetime,
        end: datetime,
        step: str = "1m"
    ) -> Dict[str, Any]:
        """
        Execute a PromQL range query

        Args:
            query: PromQL query string
            start: Start timestamp
            end: End timestamp
            step: Query resolution (e.g., "1m", "5m")

        Returns:
            Query result dictionary
        """
        url = f"{self.prometheus_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Prometheus range query failed: {response.status} - {error_text}")
                        self.stats["total_errors"] += 1
                        return {}

                    data = await response.json()
                    self.stats["total_queries"] += 1
                    return data.get('data', {})

        except Exception as e:
            logger.error(f"Error querying Prometheus range: {e}")
            self.stats["total_errors"] += 1
            return {}

    async def correlate_metrics(
        self,
        anomaly_time: datetime,
        container: str,
        context_minutes: int = 5
    ) -> Dict[str, Any]:
        """
        Correlate metrics around anomaly time

        Args:
            anomaly_time: Anomaly timestamp
            container: Container name
            context_minutes: Minutes before/after anomaly

        Returns:
            Dictionary of correlated metrics
        """
        start = anomaly_time - timedelta(minutes=context_minutes)
        end = anomaly_time + timedelta(minutes=context_minutes)

        metrics = {}

        # CPU usage
        cpu_query = f'rate(container_cpu_usage_seconds_total{{name="{container}"}}[1m])'
        cpu_data = await self.query_range(cpu_query, start, end)
        metrics['cpu'] = self._extract_metric_value(cpu_data, anomaly_time)

        # Memory usage
        mem_query = f'container_memory_usage_bytes{{name="{container}"}}'
        mem_data = await self.query_range(mem_query, start, end)
        metrics['memory_bytes'] = self._extract_metric_value(mem_data, anomaly_time)

        # Network RX
        net_rx_query = f'rate(container_network_receive_bytes_total{{name="{container}"}}[1m])'
        net_rx_data = await self.query_range(net_rx_query, start, end)
        metrics['network_rx_bytes_per_sec'] = self._extract_metric_value(net_rx_data, anomaly_time)

        # Network TX
        net_tx_query = f'rate(container_network_transmit_bytes_total{{name="{container}"}}[1m])'
        net_tx_data = await self.query_range(net_tx_query, start, end)
        metrics['network_tx_bytes_per_sec'] = self._extract_metric_value(net_tx_data, anomaly_time)

        # GPU metrics (if available)
        gpu_query = 'DCGM_FI_DEV_GPU_UTIL'
        gpu_data = await self.query_range(gpu_query, start, end)
        metrics['gpu_utilization'] = self._extract_metric_value(gpu_data, anomaly_time)

        # GPU memory
        gpu_mem_query = 'DCGM_FI_DEV_FB_USED'
        gpu_mem_data = await self.query_range(gpu_mem_query, start, end)
        metrics['gpu_memory_used_mb'] = self._extract_metric_value(gpu_mem_data, anomaly_time)

        # Analyze metrics for spikes or anomalies
        metrics['analysis'] = self._analyze_metrics(metrics)

        logger.info(f"Correlated metrics for container '{container}' at {anomaly_time}")

        return metrics

    def _extract_metric_value(
        self,
        query_result: Dict[str, Any],
        target_time: datetime
    ) -> Optional[float]:
        """
        Extract metric value closest to target time

        Args:
            query_result: Prometheus query result
            target_time: Target timestamp

        Returns:
            Metric value or None
        """
        results = query_result.get('result', [])
        if not results:
            return None

        # Get first result series
        values = results[0].get('values', [])
        if not values:
            return None

        # Find value closest to target time
        target_ts = target_time.timestamp()
        closest_value = None
        min_diff = float('inf')

        for ts, value in values:
            diff = abs(ts - target_ts)
            if diff < min_diff:
                min_diff = diff
                try:
                    closest_value = float(value)
                except (ValueError, TypeError):
                    pass

        return closest_value

    def _analyze_metrics(self, metrics: Dict[str, Any]) -> Dict[str, bool]:
        """
        Analyze metrics for anomalies

        Args:
            metrics: Dictionary of metric values

        Returns:
            Dictionary of boolean flags
        """
        analysis = {
            'cpu_spike': False,
            'memory_pressure': False,
            'network_spike': False,
            'gpu_overload': False
        }

        # CPU spike (> 80%)
        if metrics.get('cpu') and metrics['cpu'] > 0.8:
            analysis['cpu_spike'] = True

        # Memory pressure (> 80% of typical container limit, ~2GB)
        if metrics.get('memory_bytes') and metrics['memory_bytes'] > 1.6 * 1024**3:
            analysis['memory_pressure'] = True

        # Network spike (> 100 MB/s)
        net_rx = metrics.get('network_rx_bytes_per_sec', 0)
        net_tx = metrics.get('network_tx_bytes_per_sec', 0)
        if net_rx > 100 * 1024**2 or net_tx > 100 * 1024**2:
            analysis['network_spike'] = True

        # GPU overload (> 90%)
        if metrics.get('gpu_utilization') and metrics['gpu_utilization'] > 90:
            analysis['gpu_overload'] = True

        return analysis

    def get_stats(self) -> Dict[str, Any]:
        """Get correlator statistics"""
        return dict(self.stats)
