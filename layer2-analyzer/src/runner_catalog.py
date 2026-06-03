"""
Deterministic (service, operation) -> RunnerSpec / VerificationSpec mapping.

This is the Layer 2 translation layer that replaces the legacy catalog
``command_id`` strings. The System Agent (and the deterministic lab post-process)
think in terms of (service, operation); this module turns that into the
argv-first runner specs the FixingPlan 3.0 carries to Layer 4. argv targets are
the existing service wrapper scripts (invoked as plain argv, not catalog
entries) or plain read-only probes.

Removal note (legacy): previously these were catalog ``command_id`` entries like
``mysql.restore_known_good_config``; a static whitelist had no context-aware
safety. See docs/runner-v2-plan.md.
"""
from typing import Dict, List, Optional, Tuple

from schemas.action_plan import ExtractRule, RunnerSpec, VerificationSpec

WRAPPER = "/usr/local/sbin"

# systemctl unit name per service (static; replaces the agent's old dynamic
# service-name detection — lab defaults, documented in the V2 plan).
SERVICE_UNIT = {
    "nginx": "nginx",
    "postgresql": "postgresql",
    "redis": "redis-server",
    "mysql": "mysql",
}

# Read-only probe specs: (service, probe_op) -> (argv, as_root, extract, expected)
_PROBES: Dict[Tuple[str, str], Dict] = {
    ("nginx", "status"): {
        "argv": ["systemctl", "is-active", "nginx"], "as_root": False, "timeout": 10,
        "extract": {"active": {"from": "stdout_stripped", "equals": "active"}},
        "expected": {"active": True},
    },
    ("nginx", "config_test"): {
        "argv": [f"{WRAPPER}/aads-nginx-config-test"], "as_root": True, "timeout": 15,
        "extract": {"returncode": {"from": "returncode"}},
        "expected": {"returncode": 0},
    },
    ("nginx", "http_check"): {
        "argv": ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1/"],
        "as_root": False, "timeout": 10,
        "extract": {"http_code": {"from": "stdout_stripped", "as_int": True}},
        "expected": {"http_code": 200},
    },
    ("postgresql", "status"): {
        "argv": ["systemctl", "is-active", "postgresql"], "as_root": False, "timeout": 10,
        "extract": {"active": {"from": "stdout_stripped", "equals": "active"}},
        "expected": {"active": True},
    },
    ("postgresql", "connection_test"): {
        "argv": ["pg_isready"], "as_root": False, "timeout": 10,
        "extract": {"returncode": {"from": "returncode"}},
        "expected": {"returncode": 0},
    },
    ("redis", "status"): {
        "argv": ["systemctl", "is-active", "redis-server"], "as_root": False, "timeout": 10,
        "extract": {"active": {"from": "stdout_stripped", "equals": "active"}},
        "expected": {"active": True},
    },
    ("redis", "ping"): {
        "argv": ["redis-cli", "ping"], "as_root": False, "timeout": 10,
        "extract": {"pong": {"from": "stdout_stripped", "equals": "PONG"}},
        "expected": {"pong": True},
    },
    ("mysql", "status"): {
        "argv": ["systemctl", "is-active", "mysql"], "as_root": False, "timeout": 10,
        "extract": {"active": {"from": "stdout_stripped", "equals": "active"}},
        "expected": {"active": True},
    },
    ("mysql", "connection_test"): {
        "argv": ["mysqladmin", "-u", "root", "--connect-timeout=5", "ping"], "as_root": False, "timeout": 10,
        "extract": {"alive": {"from": "stdout", "contains": "alive"}},
        "expected": {"alive": True},
    },
}

# Mutating operation specs: (service, operation) -> (argv, timeout, idempotent)
_MUTATIONS: Dict[Tuple[str, str], Dict] = {
    ("nginx", "start"): {"argv": [f"{WRAPPER}/aads-nginx-start"], "timeout": 30, "idempotent": True},
    ("nginx", "reload"): {"argv": [f"{WRAPPER}/aads-nginx-reload"], "timeout": 30, "idempotent": False},
    ("nginx", "restore_config"): {"argv": [f"{WRAPPER}/aads-nginx-restore-known-good"], "timeout": 60, "idempotent": False},
    ("nginx", "ensure_snapshot"): {"argv": [f"{WRAPPER}/aads-nginx-ensure-known-good-snapshot"], "timeout": 30, "idempotent": True},
    ("postgresql", "restart"): {"argv": [f"{WRAPPER}/aads-postgresql-restart"], "timeout": 60, "idempotent": True},
    ("postgresql", "reload"): {"argv": [f"{WRAPPER}/aads-postgresql-reload"], "timeout": 30, "idempotent": False},
    ("postgresql", "restore_config"): {"argv": [f"{WRAPPER}/aads-postgresql-restore-config"], "timeout": 60, "idempotent": False},
    ("postgresql", "ensure_snapshot"): {"argv": [f"{WRAPPER}/aads-postgresql-ensure-config-snapshot"], "timeout": 30, "idempotent": True},
    ("redis", "restart"): {"argv": [f"{WRAPPER}/aads-redis-restart"], "timeout": 30, "idempotent": True},
    ("redis", "reload"): {"argv": [f"{WRAPPER}/aads-redis-reload"], "timeout": 30, "idempotent": False},
    ("redis", "restore_config"): {"argv": [f"{WRAPPER}/aads-redis-restore-config"], "timeout": 60, "idempotent": False},
    ("redis", "ensure_snapshot"): {"argv": [f"{WRAPPER}/aads-redis-ensure-config-snapshot"], "timeout": 30, "idempotent": True},
    ("mysql", "restart"): {"argv": [f"{WRAPPER}/aads-mysql-restart"], "timeout": 60, "idempotent": True},
    ("mysql", "reload"): {"argv": [f"{WRAPPER}/aads-mysql-reload"], "timeout": 30, "idempotent": False},
    ("mysql", "restore_config"): {"argv": [f"{WRAPPER}/aads-mysql-restore-config"], "timeout": 60, "idempotent": False},
    ("mysql", "ensure_snapshot"): {"argv": [f"{WRAPPER}/aads-mysql-ensure-config-snapshot"], "timeout": 30, "idempotent": True},
}

# Which read-only probe verifies a given repair operation.
_VERIFY_PROBE: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("nginx", "start"): ("nginx", "status"),
    ("nginx", "reload"): ("nginx", "config_test"),
    ("nginx", "restore_config"): ("nginx", "config_test"),
    ("postgresql", "restart"): ("postgresql", "connection_test"),
    ("postgresql", "reload"): ("postgresql", "connection_test"),
    ("postgresql", "restore_config"): ("postgresql", "connection_test"),
    ("redis", "restart"): ("redis", "ping"),
    ("redis", "reload"): ("redis", "ping"),
    ("redis", "restore_config"): ("redis", "ping"),
    ("mysql", "restart"): ("mysql", "connection_test"),
    ("mysql", "reload"): ("mysql", "connection_test"),
    ("mysql", "restore_config"): ("mysql", "connection_test"),
}

# Final verification probe per service.
_FINAL_PROBE: Dict[str, Tuple[str, str]] = {
    "nginx": ("nginx", "http_check"),
    "postgresql": ("postgresql", "connection_test"),
    "redis": ("redis", "ping"),
    "mysql": ("mysql", "connection_test"),
}

SNAPSHOT_SCOPE = {
    "nginx": "nginx_config",
    "postgresql": "postgresql_config",
    "redis": "redis_config",
    "mysql": "mysql_config",
}


def is_read_operation(service: str, operation: str) -> bool:
    return (service, operation) in _PROBES


def runner_for(service: str, operation: str) -> RunnerSpec:
    """Build the RunnerSpec for a (service, operation)."""
    if (service, operation) in _PROBES:
        spec = _PROBES[(service, operation)]
        return RunnerSpec(
            argv=list(spec["argv"]),
            as_root=spec["as_root"],
            side_effect="read",
            timeout_seconds=spec["timeout"],
        )
    if (service, operation) in _MUTATIONS:
        spec = _MUTATIONS[(service, operation)]
        return RunnerSpec(
            argv=list(spec["argv"]),
            as_root=True,
            side_effect="mutate",
            timeout_seconds=spec["timeout"],
        )
    raise KeyError(f"unknown runner operation: {service}.{operation}")


def idempotency_for(service: str, operation: str) -> Dict[str, object]:
    spec = _MUTATIONS.get((service, operation))
    idempotent = bool(spec and spec.get("idempotent"))
    return {"mode": "idempotent" if idempotent else "non_idempotent", "max_attempts": 2 if idempotent else 1}


def _verification_from_probe(service: str, probe_op: str) -> VerificationSpec:
    spec = _PROBES[(service, probe_op)]
    extract = {key: ExtractRule.model_validate(rule) for key, rule in spec["extract"].items()}
    return VerificationSpec(
        runner=RunnerSpec(
            argv=list(spec["argv"]),
            as_root=spec["as_root"],
            side_effect="read",
            timeout_seconds=spec["timeout"],
        ),
        extract=extract,
        expected=dict(spec["expected"]),
    )


def verification_for(service: str, operation: str) -> VerificationSpec:
    """Post-step verification probe for a repair operation."""
    probe = _VERIFY_PROBE.get((service, operation), ("nginx", "config_test"))
    return _verification_from_probe(probe[0], probe[1])


def final_verification_for(services: List[str]) -> VerificationSpec:
    """Service-appropriate final verification (first matching service wins)."""
    for service in ("postgresql", "redis", "mysql", "nginx"):
        if service in services:
            probe = _FINAL_PROBE[service]
            return _verification_from_probe(probe[0], probe[1])
    probe = _FINAL_PROBE["nginx"]
    return _verification_from_probe(probe[0], probe[1])


def snapshot_for(service: str) -> Tuple[RunnerSpec, str]:
    return runner_for(service, "ensure_snapshot"), SNAPSHOT_SCOPE.get(service, "nginx_config")


def rollback_for(service: str) -> Optional[RunnerSpec]:
    if (service, "restore_config") in _MUTATIONS:
        return runner_for(service, "restore_config")
    return None


def primary_service(services: List[str]) -> str:
    for service in ("postgresql", "redis", "mysql", "nginx"):
        if service in services:
            return service
    return services[0] if services else "nginx"
