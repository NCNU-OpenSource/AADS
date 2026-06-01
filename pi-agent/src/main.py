"""
AADS On-Device Agent for Ubuntu target nodes.

The agent exposes a narrow catalog API. It never accepts arbitrary shell; all
privileged mutations go through root-owned sudo wrappers installed by bootstrap.
"""
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urlrequest

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

AGENT_VERSION = "1.0.0"
SUPPORTED_SCHEMA = "1.0"
NODE_ID_PATH = Path(os.getenv("AADS_NODE_ID_PATH", "/etc/aads-agent/node-id"))
ENVIRONMENT = os.getenv("AADS_AGENT_ENVIRONMENT", "test")
SNAPSHOT_DIR = Path(os.getenv("AADS_NGINX_SNAPSHOT_DIR", "/var/lib/aads-agent/snapshots/nginx"))
PG_SNAPSHOT_DIR = Path(os.getenv("AADS_PG_SNAPSHOT_DIR", "/var/lib/aads-agent/snapshots/postgresql"))
TOKEN = os.getenv("AADS_AGENT_TOKEN", "")
ALLOWED_JOURNAL_UNITS = {
    unit.strip()
    for unit in os.getenv(
        "AADS_ALLOWED_JOURNAL_UNITS", "nginx,aads-agent,postgresql,redis,redis-server,mysql,mariadb,docker"
    ).split(",")
    if unit.strip()
}
# Docker containers allowed for restart/start actions (comma-separated)
DOCKER_ALLOWED_CONTAINERS: List[str] = [
    c.strip()
    for c in os.getenv("AADS_DOCKER_ALLOWED_CONTAINERS", "").split(",")
    if c.strip()
]

WRAPPERS = {
    "nginx.start": "/usr/local/sbin/aads-nginx-start",
    "nginx.reload": "/usr/local/sbin/aads-nginx-reload",
    "nginx.config_test": "/usr/local/sbin/aads-nginx-config-test",
    "nginx.ensure_known_good_snapshot": "/usr/local/sbin/aads-nginx-ensure-known-good-snapshot",
    "nginx.restore_known_good_config": "/usr/local/sbin/aads-nginx-restore-known-good",
    "postgresql.restart": "/usr/local/sbin/aads-postgresql-restart",
    "postgresql.reload": "/usr/local/sbin/aads-postgresql-reload",
    "postgresql.config_test": "/usr/local/sbin/aads-postgresql-config-test",
    "postgresql.ensure_config_snapshot": "/usr/local/sbin/aads-postgresql-ensure-config-snapshot",
    "postgresql.restore_known_good_config": "/usr/local/sbin/aads-postgresql-restore-config",
    # Redis
    "redis.restart": "/usr/local/sbin/aads-redis-restart",
    "redis.reload": "/usr/local/sbin/aads-redis-reload",
    "redis.config_test": "/usr/local/sbin/aads-redis-config-test",
    "redis.ensure_config_snapshot": "/usr/local/sbin/aads-redis-ensure-config-snapshot",
    "redis.restore_known_good_config": "/usr/local/sbin/aads-redis-restore-config",
    # Docker
    "docker.container_restart": "/usr/local/sbin/aads-docker-container-restart",
    "docker.container_start": "/usr/local/sbin/aads-docker-container-start",
    # MySQL / MariaDB
    "mysql.restart": "/usr/local/sbin/aads-mysql-restart",
    "mysql.reload": "/usr/local/sbin/aads-mysql-reload",
    "mysql.config_test": "/usr/local/sbin/aads-mysql-config-test",
    "mysql.ensure_config_snapshot": "/usr/local/sbin/aads-mysql-ensure-config-snapshot",
    "mysql.restore_known_good_config": "/usr/local/sbin/aads-mysql-restore-config",
}

CATALOG: Dict[str, Dict[str, Any]] = {
    "nginx.status": {
        "command_id": "nginx.status",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "nginx.config_test": {
        "command_id": "nginx.config_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 15,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": WRAPPERS["nginx.config_test"],
    },
    "system.journal_tail": {
        "command_id": "system.journal_tail",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {"unit": "string", "lines": "integer"},
        "arg_allowlist": {"unit": sorted(ALLOWED_JOURNAL_UNITS), "lines_max": 200},
        "timeout_seconds": 15,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "nginx.http_check": {
        "command_id": "nginx.http_check",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {"url": "string", "expected_status": "integer"},
        "arg_allowlist": {"url_prefixes": ["http://127.0.0.1/", "http://localhost/"], "expected_status": [200, 204, 301, 302]},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "nginx.ensure_known_good_snapshot": {
        "command_id": "nginx.ensure_known_good_snapshot",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["nginx.ensure_known_good_snapshot"],
    },
    "nginx.start": {
        "command_id": "nginx.start",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["nginx.start"],
    },
    "nginx.reload": {
        "command_id": "nginx.reload",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["nginx.reload"],
    },
    "nginx.restore_known_good_config": {
        "command_id": "nginx.restore_known_good_config",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "low",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["nginx.restore_known_good_config"],
    },
    # ── PostgreSQL ────────────────────────────────────────────
    "postgresql.status": {
        "command_id": "postgresql.status",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "postgresql.connection_test": {
        "command_id": "postgresql.connection_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "postgresql.config_test": {
        "command_id": "postgresql.config_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 15,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": WRAPPERS["postgresql.config_test"],
    },
    "postgresql.ensure_config_snapshot": {
        "command_id": "postgresql.ensure_config_snapshot",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["postgresql.ensure_config_snapshot"],
    },
    "postgresql.restart": {
        "command_id": "postgresql.restart",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["postgresql.restart"],
    },
    "postgresql.reload": {
        "command_id": "postgresql.reload",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["postgresql.reload"],
    },
    "postgresql.restore_known_good_config": {
        "command_id": "postgresql.restore_known_good_config",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["postgresql.restore_known_good_config"],
    },
    # ── Redis ─────────────────────────────────────────────────
    "redis.status": {
        "command_id": "redis.status",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "redis.ping": {
        "command_id": "redis.ping",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "redis.config_test": {
        "command_id": "redis.config_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 15,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": WRAPPERS["redis.config_test"],
    },
    "redis.ensure_config_snapshot": {
        "command_id": "redis.ensure_config_snapshot",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["redis.ensure_config_snapshot"],
    },
    "redis.restart": {
        "command_id": "redis.restart",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["redis.restart"],
    },
    "redis.reload": {
        "command_id": "redis.reload",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["redis.reload"],
    },
    "redis.restore_known_good_config": {
        "command_id": "redis.restore_known_good_config",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["redis.restore_known_good_config"],
    },
    # ── Docker Container ──────────────────────────────────────
    "docker.container_status": {
        "command_id": "docker.container_status",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {"container": "string"},
        "arg_allowlist": {"container": DOCKER_ALLOWED_CONTAINERS},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "docker.container_restart": {
        "command_id": "docker.container_restart",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {"container": "string"},
        "arg_allowlist": {"container": DOCKER_ALLOWED_CONTAINERS},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["docker.container_restart"],
    },
    "docker.container_start": {
        "command_id": "docker.container_start",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {"container": "string"},
        "arg_allowlist": {"container": DOCKER_ALLOWED_CONTAINERS},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["docker.container_start"],
    },
    # ── MySQL / MariaDB ───────────────────────────────────────
    "mysql.status": {
        "command_id": "mysql.status",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "mysql.connection_test": {
        "command_id": "mysql.connection_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 10,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": None,
    },
    "mysql.config_test": {
        "command_id": "mysql.config_test",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "probe",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 15,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 3},
        "sudo_wrapper": WRAPPERS["mysql.config_test"],
    },
    "mysql.ensure_config_snapshot": {
        "command_id": "mysql.ensure_config_snapshot",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["mysql.ensure_config_snapshot"],
    },
    "mysql.restart": {
        "command_id": "mysql.restart",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": True,
        "retry_policy": {"max_attempts": 2},
        "sudo_wrapper": WRAPPERS["mysql.restart"],
    },
    "mysql.reload": {
        "command_id": "mysql.reload",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 30,
        "risk_level": "low",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["mysql.reload"],
    },
    "mysql.restore_known_good_config": {
        "command_id": "mysql.restore_known_good_config",
        "schema_version": SUPPORTED_SCHEMA,
        "scope": "action",
        "args_schema": {},
        "arg_allowlist": {},
        "timeout_seconds": 60,
        "risk_level": "medium",
        "idempotent": False,
        "retry_policy": {"max_attempts": 1},
        "sudo_wrapper": WRAPPERS["mysql.restore_known_good_config"],
    },
}


class CommandRequest(BaseModel):
    command_id: str
    schema_version: str = SUPPORTED_SCHEMA
    args: Dict[str, Any] = Field(default_factory=dict)


class AgentTaskRequest(BaseModel):
    task_type: str
    schema_version: str = SUPPORTED_SCHEMA
    idempotency_key: str
    args: Dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="AADS On-Device Agent", version=AGENT_VERSION)


def require_auth(authorization: Optional[str] = Header(default=None)):
    if not TOKEN:
        raise HTTPException(status_code=500, detail="AADS_AGENT_TOKEN is not configured")
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


def node_id() -> str:
    if NODE_ID_PATH.exists():
        return NODE_ID_PATH.read_text().strip()
    return "unknown"


@app.get("/health")
async def health(_: None = Depends(require_auth)):
    problems = health_problems()
    if problems:
        raise HTTPException(status_code=503, detail={"status": "unhealthy", "problems": problems})
    return {"status": "healthy", "node_id": node_id(), "environment": ENVIRONMENT, "agent_version": AGENT_VERSION}


@app.get("/v1/node/facts")
async def facts(_: None = Depends(require_auth)):
    return {
        "node_id": node_id(),
        "environment": ENVIRONMENT,
        "agent_version": AGENT_VERSION,
        "supported_commands": list(CATALOG.values()),
    }


@app.post("/v1/probes/run")
async def run_probe(req: CommandRequest, _: None = Depends(require_auth)):
    meta = validate_request(req, expected_scope="probe")
    return run_command(meta, req.args)


@app.post("/v1/actions/run")
async def run_action(req: CommandRequest, _: None = Depends(require_auth)):
    meta = validate_request(req, expected_scope="action")
    return run_command(meta, req.args)


@app.post("/v1/agent-tasks/run")
async def run_agent_task(req: AgentTaskRequest, _: None = Depends(require_auth)):
    """
    Reserved AgentTask dispatch contract for On-Device Agent RCA.

    v1 exposes the wire shape and idempotency key requirement, but does not run
    target-side LLM RCA yet. Controller-side System Agent can depend on this
    contract without pushing FixingPlan execution state to the target.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "missing_idempotency_key"})
    if req.task_type != "rca":
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "unsupported_task_type"})
    raise HTTPException(status_code=501, detail={"status": "blocked", "reason": "rca_agent_task_not_enabled_in_v1"})


def validate_request(req: CommandRequest, expected_scope: str) -> Dict[str, Any]:
    meta = CATALOG.get(req.command_id)
    if not meta:
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "unknown_command"})
    if req.schema_version != meta["schema_version"]:
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "unsupported_schema"})
    if meta["scope"] != expected_scope:
        raise HTTPException(status_code=403, detail={"status": "blocked", "reason": "wrong_scope"})
    validate_args(meta, req.args)
    if expected_scope == "action" and not snapshot_available() and req.command_id == "nginx.restore_known_good_config":
        raise HTTPException(status_code=409, detail={"status": "blocked", "reason": "no_snapshot"})
    return meta


def validate_args(meta: Dict[str, Any], args: Dict[str, Any]):
    if meta["command_id"] == "system.journal_tail":
        unit = args.get("unit", "nginx")
        lines = int(args.get("lines", 80))
        if unit not in ALLOWED_JOURNAL_UNITS:
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "unit_not_allowed"})
        if lines < 1 or lines > 200:
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "lines_out_of_range"})
    elif meta["command_id"] == "nginx.http_check":
        url = args.get("url", "http://127.0.0.1/")
        expected_status = int(args.get("expected_status", 200))
        prefixes = meta["arg_allowlist"]["url_prefixes"]
        if not any(str(url).startswith(prefix) for prefix in prefixes):
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "url_not_allowed"})
        if expected_status not in meta["arg_allowlist"]["expected_status"]:
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "status_not_allowed"})
    elif meta["command_id"] in ("docker.container_restart", "docker.container_start", "docker.container_status"):
        container = args.get("container", "")
        allowed = meta["arg_allowlist"].get("container", [])
        if not container:
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "missing_container_name"})
        if container not in allowed:
            raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "container_not_allowed"})
    elif args:
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "args_not_allowed"})


def run_command(meta: Dict[str, Any], args: Dict[str, Any]):
    command_id = meta["command_id"]
    if command_id == "nginx.status":
        return nginx_status(meta["timeout_seconds"])
    if command_id == "nginx.http_check":
        return nginx_http_check(args, meta["timeout_seconds"])
    if command_id in ("nginx.config_test", "postgresql.config_test", "redis.config_test", "mysql.config_test"):
        return exec_cmd(["sudo", "-n", meta["sudo_wrapper"]], meta["timeout_seconds"], retryable=True)
    if command_id == "system.journal_tail":
        unit = args.get("unit", "nginx")
        lines = str(int(args.get("lines", 80)))
        return exec_cmd(["journalctl", "-u", unit, "-n", lines, "--no-pager"], meta["timeout_seconds"], retryable=True)
    # Service status probes
    if command_id == "postgresql.status":
        return service_status("postgresql", meta["timeout_seconds"])
    if command_id == "postgresql.connection_test":
        return postgresql_connection_test(meta["timeout_seconds"])
    if command_id == "redis.status":
        svc = "redis-server" if _service_exists("redis-server") else "redis"
        return service_status(svc, meta["timeout_seconds"])
    if command_id == "redis.ping":
        return redis_ping(meta["timeout_seconds"])
    if command_id == "mysql.status":
        svc = "mysql" if _service_exists("mysql") else "mariadb"
        return service_status(svc, meta["timeout_seconds"])
    if command_id == "mysql.connection_test":
        return mysql_connection_test(meta["timeout_seconds"])
    # Docker container probes/actions (pass container name as argument to wrapper)
    if command_id == "docker.container_status":
        container = args.get("container", "")
        return docker_container_status(container, meta["timeout_seconds"])
    if command_id in ("docker.container_restart", "docker.container_start"):
        container = args.get("container", "")
        return exec_cmd(["sudo", "-n", meta["sudo_wrapper"], container], meta["timeout_seconds"], retryable=False)

    wrapper = meta["sudo_wrapper"]
    return exec_cmd(["sudo", "-n", wrapper], meta["timeout_seconds"], retryable=False)


def nginx_status(timeout: int):
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", "nginx"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        state = (completed.stdout or completed.stderr).strip()
        if completed.returncode in {0, 3}:
            return {
                "status": "success",
                "returncode": completed.returncode,
                "state": state or "unknown",
                "active": completed.returncode == 0,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "retryable": False,
            }
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "returncode": completed.returncode,
                "state": state or "unknown",
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "retryable": True,
            },
        )
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def nginx_http_check(args: Dict[str, Any], timeout: int):
    url = args.get("url", "http://127.0.0.1/")
    expected_status = int(args.get("expected_status", 200))
    try:
        with urlrequest.urlopen(url, timeout=timeout) as response:
            http_status = int(response.status)
            ok = http_status == expected_status
            payload = {
                "status": "success" if ok else "failed",
                "http_status": http_status,
                "expected_status": expected_status,
                "url": url,
                "checks": {"http_status": ok},
                "retryable": not ok,
            }
            if not ok:
                raise HTTPException(status_code=500, detail=payload)
            return payload
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "status": "failed",
                "url": url,
                "expected_status": expected_status,
                "reason": str(e),
                "checks": {"http_status": False},
                "retryable": True,
            },
        )


def exec_cmd(argv: List[str], timeout: int, retryable: bool):
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        payload = {
            "status": "success" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "checks": {"returncode": completed.returncode == 0},
            "retryable": retryable and completed.returncode != 0,
        }
        if completed.returncode != 0:
            raise HTTPException(status_code=500 if retryable else 409, detail=payload)
        return payload
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def service_status(service: str, timeout: int):
    """Generic systemctl is-active probe for any service."""
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        state = (completed.stdout or completed.stderr).strip()
        if completed.returncode in {0, 3}:
            return {
                "status": "success",
                "returncode": completed.returncode,
                "state": state or "unknown",
                "active": completed.returncode == 0,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
                "retryable": False,
            }
        raise HTTPException(status_code=500, detail={
            "status": "failed", "returncode": completed.returncode,
            "state": state or "unknown", "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:], "retryable": True,
        })
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def postgresql_connection_test(timeout: int):
    """Run pg_isready to verify PostgreSQL is accepting connections."""
    try:
        completed = subprocess.run(
            ["pg_isready", "-h", "localhost"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        accepting = completed.returncode == 0
        payload = {
            "status": "success" if accepting else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "checks": {"accepting_connections": accepting},
            "retryable": not accepting,
        }
        if not accepting:
            raise HTTPException(status_code=500, detail=payload)
        return payload
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def _service_exists(service: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "cat", service], capture_output=True, timeout=5, check=False)
        return r.returncode == 0
    except Exception:
        return False


def redis_ping(timeout: int):
    try:
        completed = subprocess.run(
            ["redis-cli", "ping"], capture_output=True, text=True, timeout=timeout, check=False,
        )
        pong = completed.stdout.strip() == "PONG"
        payload = {
            "status": "success" if pong else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "checks": {"pong": pong},
            "retryable": not pong,
        }
        if not pong:
            raise HTTPException(status_code=500, detail=payload)
        return payload
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def mysql_connection_test(timeout: int):
    try:
        completed = subprocess.run(
            ["mysqladmin", "-u", "root", "--connect-timeout=5", "ping"],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        ok = completed.returncode == 0
        payload = {
            "status": "success" if ok else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "checks": {"accepting_connections": ok},
            "retryable": not ok,
        }
        if not ok:
            raise HTTPException(status_code=500, detail=payload)
        return payload
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def docker_container_status(container: str, timeout: int):
    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
        state = completed.stdout.strip()
        running = state == "running"
        payload = {
            "status": "success" if running else "failed",
            "returncode": completed.returncode,
            "container": container,
            "state": state or "unknown",
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "checks": {"running": running},
            "retryable": not running,
        }
        if not running:
            raise HTTPException(status_code=500, detail=payload)
        return payload
    except subprocess.TimeoutExpired as e:
        raise HTTPException(status_code=504, detail={"status": "timeout", "stdout": e.stdout, "stderr": e.stderr, "retryable": True})


def snapshot_available() -> bool:
    try:
        return (SNAPSHOT_DIR / "nginx.conf").exists() and (SNAPSHOT_DIR / "sites-enabled").exists()
    except PermissionError:
        return False


def health_problems() -> List[str]:
    problems: List[str] = []
    if not NODE_ID_PATH.exists():
        problems.append(f"missing node id file: {NODE_ID_PATH}")
    for command_id, wrapper in WRAPPERS.items():
        path = Path(wrapper)
        if not path.exists():
            problems.append(f"missing wrapper for {command_id}: {wrapper}")
            continue
        st = path.stat()
        if st.st_uid != 0 or st.st_gid != 0:
            problems.append(f"wrapper not root-owned: {wrapper}")
        if st.st_mode & stat.S_IWGRP or st.st_mode & stat.S_IWOTH:
            problems.append(f"wrapper writable by group/other: {wrapper}")
    if not snapshot_available():
        problems.append(f"missing nginx known-good snapshot in {SNAPSHOT_DIR}")
    problems.extend(sudoers_health_problems())
    # PostgreSQL snapshot is optional (service may not be installed)
    # — only warn, not error, so nginx-only nodes stay healthy
    return problems


def sudoers_health_problems() -> List[str]:
    problems: List[str] = []
    sudoers = Path("/etc/sudoers.d/aads-agent")
    try:
        if not sudoers.exists():
            problems.append("missing sudoers file: /etc/sudoers.d/aads-agent")
        else:
            st = sudoers.stat()
            if st.st_uid != 0 or st.st_gid != 0:
                problems.append("sudoers file is not root-owned")
            if stat.S_IMODE(st.st_mode) != 0o440:
                problems.append("sudoers file mode must be 0440")
    except PermissionError:
        # Ubuntu commonly protects /etc/sudoers.d from unprivileged stat().
        # Fall back to checking the effective exact-command grants.
        pass
    for command_id, wrapper in WRAPPERS.items():
        try:
            completed = subprocess.run(
                ["sudo", "-n", "-l", wrapper],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            problems.append(f"sudoers check failed for {command_id}: {e}")
            continue
        if completed.returncode != 0:
            reason = (completed.stderr or completed.stdout).strip()
            problems.append(f"sudoers does not allow {command_id}: {reason}")
    return problems
