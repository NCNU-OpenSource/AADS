"""
Agent Tools for LangGraph Layer 2 Investigator

Provides read-only tools for the Agent to gather additional context:
- query_loki: Query Loki logs using LogQL
- query_prometheus: Query Prometheus metrics using PromQL
- k8s_exec_mock: Mock Kubernetes command execution (safe for initial testing)

All tools are designed to be:
1. Read-only by default (safe to auto-execute)
2. Idempotent (can be called multiple times without side effects)
3. Timeout-protected (prevents Agent from hanging)

Related: Phase 4.3
"""
import os
import logging
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from langchain_core.tools import tool

import log_guard

logger = logging.getLogger(__name__)

# Configuration
LOKI_URL = os.getenv("LOKI_URL", "http://loki:3100")
LOKI_TENANT = os.getenv("LOKI_TENANT", "raw")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")


@tool
async def query_loki(query: str, start_time: str, end_time: str, limit: int = 100) -> str:
    """
    Query Loki logs using LogQL

    Args:
        query: LogQL query string (e.g., '{container="nginx"} |= "error"')
        start_time: Start time in ISO format (e.g., "2026-04-08T10:00:00Z")
        end_time: End time in ISO format
        limit: Maximum number of log lines to return (default: 100)

    Returns:
        Formatted log lines as a string

    Example:
        query_loki('{container="nginx"}', '2026-04-08T10:00:00Z', '2026-04-08T10:05:00Z')
    """
    try:
        # Convert ISO strings to nanosecond timestamps for Loki
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        start_ns = int(start_dt.timestamp() * 1e9)
        end_ns = int(end_dt.timestamp() * 1e9)

        url = f"{LOKI_URL}/loki/api/v1/query_range"
        params = {
            'query': query,
            'start': start_ns,
            'end': end_ns,
            'limit': limit
        }
        headers = {'X-Scope-OrgID': LOKI_TENANT}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Loki query failed: {response.status} - {error_text}")
                    return f"Error querying Loki: {response.status}"

                data = await response.json()
                result = data.get('data', {}).get('result', [])

                if not result:
                    return "No logs found for the given query and time range."

                # Format logs
                log_lines = []
                for stream in result:
                    labels = stream.get('stream', {})
                    container = labels.get('container', 'unknown')
                    for value in stream.get('values', []):
                        timestamp_ns, log_line = value
                        timestamp = datetime.fromtimestamp(int(timestamp_ns) / 1e9).isoformat()
                        log_lines.append(f"[{timestamp}] [{container}] {log_line}")

                if len(log_lines) > limit:
                    log_lines = log_lines[:limit]
                    log_lines.append(f"... (truncated to {limit} lines)")

                # Log content is attacker-writable: scan for injection, taint
                # the investigation on hits, and fence the data (ADR-007).
                return log_guard.guard_tool_output("\n".join(log_lines), "loki")

    except Exception as e:
        logger.error(f"Error in query_loki: {e}")
        return f"Error: {str(e)}"


@tool
async def query_prometheus(promql: str, time: Optional[str] = None) -> str:
    """
    Query Prometheus metrics using PromQL

    Args:
        promql: PromQL query string (e.g., 'rate(http_requests_total[5m])')
        time: Optional query time in ISO format (defaults to now)

    Returns:
        Formatted metric results as a string

    Example:
        query_prometheus('rate(container_cpu_usage_seconds_total{container="nginx"}[5m])')
    """
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query"
        params = {'query': promql}

        if time:
            time_dt = datetime.fromisoformat(time.replace('Z', '+00:00'))
            params['time'] = int(time_dt.timestamp())

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Prometheus query failed: {response.status} - {error_text}")
                    return f"Error querying Prometheus: {response.status}"

                data = await response.json()
                result = data.get('data', {}).get('result', [])

                if not result:
                    return "No metrics found for the given query."

                # Format metrics
                metric_lines = []
                for metric in result:
                    labels = metric.get('metric', {})
                    value = metric.get('value', [])
                    if len(value) == 2:
                        timestamp, metric_value = value
                        label_str = ", ".join([f"{k}={v}" for k, v in labels.items()])
                        metric_lines.append(f"{label_str} = {metric_value}")

                # Metric labels can carry attacker-influenced strings too.
                return log_guard.guard_tool_output("\n".join(metric_lines), "prometheus")

    except Exception as e:
        logger.error(f"Error in query_prometheus: {e}")
        return f"Error: {str(e)}"


@tool
async def execute_diagnostic_command(command: str) -> str:
    """
    Execute read-only Linux diagnostic commands for system investigation

    SECURITY: Only whitelisted read-only commands are allowed.
    Commands are executed with a 30-second timeout.

    Allowed command prefixes:
    - ps, top, htop: Process inspection
    - netstat, ss, lsof: Network diagnostics
    - df, du, free: Resource usage
    - docker ps, docker stats, docker logs: Docker inspection
    - journalctl, dmesg: System logs
    - curl (localhost only), wget (localhost only): Service health checks
    - uptime, who, w: System status

    Args:
        command: Diagnostic command to execute (will be validated against whitelist)

    Returns:
        Command output or error message

    Examples:
        execute_diagnostic_command('ps aux | grep nginx')
        execute_diagnostic_command('docker ps --format "{{.Names}}: {{.Status}}"')
        execute_diagnostic_command('curl -s http://localhost:8080/health')
    """
    import subprocess
    import asyncio

    # Whitelist of allowed command prefixes (read-only operations)
    ALLOWED_COMMANDS = [
        'ps', 'top', 'htop',                    # Process inspection
        'netstat', 'ss', 'lsof',                # Network diagnostics
        'df', 'du', 'free', 'vmstat', 'iostat', # Resource usage
        'docker ps', 'docker stats', 'docker logs', 'docker inspect',  # Docker (read-only)
        'journalctl', 'dmesg', 'tail', 'head', 'cat',  # Logs (restricted paths)
        'uptime', 'who', 'w', 'last',           # System status
        'curl', 'wget',                         # Service checks (localhost only)
        'systemctl status',                     # Service status
    ]

    # Validate command against whitelist
    command_safe = command.strip()
    if not any(command_safe.startswith(allowed) for allowed in ALLOWED_COMMANDS):
        return f"BLOCKED: Command '{command}' is not in the allowed whitelist. Only read-only diagnostic commands are permitted."

    # Extra safety: Block dangerous patterns
    FORBIDDEN_PATTERNS = ['rm ', 'dd ', 'mkfs', '>', '>>', 'sudo', 'chmod', 'chown']
    if any(pattern in command_safe.lower() for pattern in FORBIDDEN_PATTERNS):
        return f"BLOCKED: Command contains forbidden pattern. Only read-only operations allowed."

    # Restrict curl/wget to localhost only
    if command_safe.startswith(('curl', 'wget')):
        if not any(host in command_safe for host in ['localhost', '127.0.0.1', '0.0.0.0']):
            return "BLOCKED: curl/wget only allowed for localhost endpoints"

    logger.info(f"[DIAGNOSTIC] Executing: {command_safe}")

    try:
        # Execute command with timeout
        process = await asyncio.create_subprocess_shell(
            command_safe,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            process.kill()
            return f"TIMEOUT: Command execution exceeded 30 seconds"

        # Decode output
        output = stdout.decode('utf-8', errors='replace')
        error = stderr.decode('utf-8', errors='replace')

        if process.returncode != 0:
            logger.warning(f"Command failed with code {process.returncode}: {error}")
            return log_guard.guard_tool_output(
                f"Command failed (exit code {process.returncode}):\n{error}\n\nPartial output:\n{output}",
                "diagnostic_command",
            )

        # Limit output size to prevent token explosion
        if len(output) > 10000:
            output = output[:10000] + f"\n... (truncated, total {len(output)} chars)"

        return log_guard.guard_tool_output(output, "diagnostic_command") if output else "(No output)"

    except Exception as e:
        logger.error(f"Error executing diagnostic command: {e}")
        return f"Error: {str(e)}"


# Export tools list for LangGraph
AGENT_TOOLS = [query_loki, query_prometheus, execute_diagnostic_command]
