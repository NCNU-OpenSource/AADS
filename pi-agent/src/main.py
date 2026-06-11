"""
AADS On-Device Agent for Ubuntu target nodes.

V2: argv-first full-power Command Runner + context-aware Hook pipeline. This
replaces the legacy catalog command whitelist.

Removal note (legacy): the old contract exposed a static ``CATALOG`` of
``command_id`` entries and ``/v1/probes/run`` + ``/v1/actions/run``. A static
command whitelist has no context-aware safety — "safe" was bound to "is this
command_id present". The new safety boundary is the full per-execution runner
spec + context + hook decision + append-only audit. See docs/runner-v2-plan.md.

Execution rules:
- argv is run with ``shell=False``; no implicit shell expansion, pipe, or redirect.
- ``as_root=true`` is routed through a single root-owned runner wrapper that
  sudoers authorizes exactly; it still execs argv (shell=False), never a shell
  string. Existing service wrapper scripts remain as operational helpers invoked
  as plain argv, not catalog entries.
- Hooks run ``before_run`` -> execute -> ``after_run``/``on_error``. v1 ships the
  built-in ``AuditHook`` (always allow, fully audit). Safety cards (deny) land
  later under ``pi-agent/src/safety_cards/``.
"""
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

try:
    from safety_cards import ExecutionProfile, HookDecision, PolicyCard
    from safety_cards import policy_card as _policy_card_module
except ImportError:  # loaded as a bare file (tests / single-file layouts)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from safety_cards import ExecutionProfile, HookDecision, PolicyCard
    from safety_cards import policy_card as _policy_card_module

AGENT_VERSION = "2.1.0"
RUNNER_SCHEMA = "runner.v1"
NODE_ID_PATH = Path(os.getenv("AADS_NODE_ID_PATH", "/etc/aads-agent/node-id"))
ENVIRONMENT = os.getenv("AADS_AGENT_ENVIRONMENT", "test")
TOKEN = os.getenv("AADS_AGENT_TOKEN", "")
ROOT_RUNNER = os.getenv("AADS_ROOT_RUNNER", "/usr/local/sbin/aads-root-command-runner")
SUDOERS_FILE = Path("/etc/sudoers.d/aads-agent")
MAX_TIMEOUT = int(os.getenv("AADS_MAX_TIMEOUT_SECONDS", "600"))
OUTPUT_LIMIT = 4000


# ── Request / response models ─────────────────────────────────
class RunnerSpec(BaseModel):
    mode: str = "argv"
    argv: List[str] = Field(..., min_length=1)
    cwd: str = "/"
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 30
    as_root: bool = False
    side_effect: str = "read"  # read | mutate (advisory; Layer 4 uses it for locking)
    stdin: Optional[str] = None


class CommandContext(BaseModel):
    purpose: Optional[str] = None
    service: Optional[str] = None
    operation: Optional[str] = None
    plan_id: Optional[str] = None
    step_id: Optional[int] = None
    evidence_refs: List[str] = Field(default_factory=list)


class CommandRunRequest(BaseModel):
    schema_version: str = RUNNER_SCHEMA
    runner: RunnerSpec
    context: CommandContext = Field(default_factory=CommandContext)
    idempotency: Dict[str, Any] = Field(default_factory=dict)
    # ExecutionProfile permission manifest (ADR-005): generated at plan time,
    # human-approved with the plan, enforced by the PolicyCard per request.
    execution_profile: Optional[ExecutionProfile] = None


class AgentTaskRequest(BaseModel):
    task_type: str
    schema_version: str = RUNNER_SCHEMA
    idempotency_key: str
    args: Dict[str, Any] = Field(default_factory=dict)


# ── Hook pipeline ─────────────────────────────────────────────
# HookDecision lives in safety_cards.policy_card (shared hook contract).


class AuditHook:
    """
    Built-in audit hook: always allow, fully audit. Runs LAST in the pipeline,
    after the PolicyCard has had the chance to deny; the decision + context +
    argv hash are surfaced so Layer 4 can write the audit trail.
    """

    card_id = "audit.allow_all.v1"

    def before_run(self, request: CommandRunRequest, argv_sha: str) -> HookDecision:
        return HookDecision(
            decision="allow",
            card_id=self.card_id,
            reason="audit-only allow-all (v1, no safety card configured)",
            annotations={
                "argv_sha256": argv_sha,
                "as_root": request.runner.as_root,
                "side_effect": request.runner.side_effect,
            },
        )

    def after_run(self, request: CommandRunRequest, result: Dict[str, Any]) -> HookDecision:
        return HookDecision(decision="allow", card_id=self.card_id, reason="post-run audit")

    def on_error(self, request: CommandRunRequest, error: str) -> HookDecision:
        return HookDecision(decision="allow", card_id=self.card_id, reason=f"error audit: {error}")


# PolicyCard first (can deny), AuditHook last (always allows, always audits).
HOOKS: List[Any] = [PolicyCard(), AuditHook()]


def argv_sha256(argv: List[str]) -> str:
    return hashlib.sha256("\x00".join(argv).encode("utf-8", "replace")).hexdigest()


# ── Command runner ────────────────────────────────────────────
class CommandRunner:
    """
    Executes a RunnerSpec with ``shell=False``. ``as_root`` routes through the
    single root runner wrapper, passing argv/cwd/env/timeout as JSON over stdin.
    """

    def run(self, spec: RunnerSpec) -> Dict[str, Any]:
        timeout = max(1, min(int(spec.timeout_seconds), MAX_TIMEOUT))
        if spec.as_root:
            argv = ["sudo", "-n", ROOT_RUNNER]
            stdin_data = json.dumps(
                {"argv": list(spec.argv), "cwd": spec.cwd, "env": spec.env, "timeout": timeout}
            )
            run_cwd = None  # root runner chdir's per JSON
            run_env = None  # root runner applies env per JSON
        else:
            argv = list(spec.argv)
            stdin_data = spec.stdin
            run_cwd = spec.cwd or None
            run_env = {**os.environ, **spec.env} if spec.env else None

        start = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
                cwd=run_cwd,
                env=run_env,
                input=stdin_data,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "status": "timeout",
                "returncode": None,
                "stdout": (exc.stdout or "")[-OUTPUT_LIMIT:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-OUTPUT_LIMIT:] if isinstance(exc.stderr, str) else "",
                "duration_ms": int((time.monotonic() - start) * 1000),
                "timed_out": True,
            }
        return {
            "status": "success" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "stdout": (completed.stdout or "")[-OUTPUT_LIMIT:],
            "stderr": (completed.stderr or "")[-OUTPUT_LIMIT:],
            "duration_ms": int((time.monotonic() - start) * 1000),
            "timed_out": False,
        }


RUNNER = CommandRunner()
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
        "runner_capabilities": {
            "schema_version": RUNNER_SCHEMA,
            "modes": ["argv"],
            "supports_as_root": True,
            "hook_default": "policy_card+audit",
            "policy_mode": _policy_card_module.POLICY_MODE,
        },
    }


@app.post("/v1/commands/run")
async def run_command(req: CommandRunRequest, _: None = Depends(require_auth)):
    """
    Execute one runner spec through the hook pipeline.

    Always responds 200 with a structured body (status / returncode / stdout /
    stderr) once the request authenticates, even when the command exits non-zero.
    This lets Layer 4 verification extract structured fields from a probe that
    "fails" by design (e.g. ``systemctl is-active`` returning ``inactive``). Auth
    failures (401) and a hook ``deny`` are the only non-execution outcomes.
    """
    sha = argv_sha256(req.runner.argv)
    decisions: List[HookDecision] = []
    for hook in HOOKS:
        decision = hook.before_run(req, sha)
        decisions.append(decision)
        if decision.decision == "deny":
            return {
                "status": "blocked",
                "reason": "hook_denied",
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "hook": _hook_payload(decision, req, sha),
                "context": req.context.model_dump(),
                "retryable": False,
            }

    result = RUNNER.run(req.runner)

    for hook in HOOKS:
        hook.after_run(req, result)

    # Surface the most significant verdict (warn beats allow) so an audit-mode
    # policy violation stays visible in the response and the Layer 4 audit trail.
    surfaced = next((d for d in decisions if d.decision == "warn"), decisions[-1] if decisions else None)

    return {
        **result,
        "hook": _hook_payload(surfaced, req, sha),
        "context": req.context.model_dump(),
        "retryable": result["status"] != "success",
    }


def _hook_payload(decision: Optional[HookDecision], req: CommandRunRequest, sha: str) -> Dict[str, Any]:
    base = decision.model_dump() if decision else {"decision": "allow", "card_id": "none"}
    base["argv_sha256"] = sha
    base["as_root"] = req.runner.as_root
    base["side_effect"] = req.runner.side_effect
    return base


@app.post("/v1/agent-tasks/run")
async def run_agent_task(req: AgentTaskRequest, _: None = Depends(require_auth)):
    """
    Reserved AgentTask dispatch contract for On-Device Agent RCA.

    v1 exposes the wire shape and idempotency key requirement, but does not run
    target-side LLM RCA yet.
    """
    if not req.idempotency_key:
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "missing_idempotency_key"})
    if req.task_type != "rca":
        raise HTTPException(status_code=400, detail={"status": "blocked", "reason": "unsupported_task_type"})
    raise HTTPException(status_code=501, detail={"status": "blocked", "reason": "rca_agent_task_not_enabled_in_v1"})


def health_problems() -> List[str]:
    problems: List[str] = []
    if not NODE_ID_PATH.exists():
        problems.append(f"missing node id file: {NODE_ID_PATH}")
    runner = Path(ROOT_RUNNER)
    if not runner.exists():
        problems.append(f"missing root runner wrapper: {ROOT_RUNNER}")
    else:
        st = runner.stat()
        if st.st_uid != 0 or st.st_gid != 0:
            problems.append(f"root runner not root-owned: {ROOT_RUNNER}")
        if st.st_mode & stat.S_IWGRP or st.st_mode & stat.S_IWOTH:
            problems.append(f"root runner writable by group/other: {ROOT_RUNNER}")
    problems.extend(sudoers_health_problems())
    return problems


def sudoers_health_problems() -> List[str]:
    problems: List[str] = []
    try:
        if not SUDOERS_FILE.exists():
            problems.append("missing sudoers file: /etc/sudoers.d/aads-agent")
        else:
            st = SUDOERS_FILE.stat()
            if st.st_uid != 0 or st.st_gid != 0:
                problems.append("sudoers file is not root-owned")
            if stat.S_IMODE(st.st_mode) != 0o440:
                problems.append("sudoers file mode must be 0440")
    except PermissionError:
        # Ubuntu commonly protects /etc/sudoers.d from unprivileged stat().
        # Fall back to checking the effective grant for the root runner.
        pass
    try:
        completed = subprocess.run(
            ["sudo", "-n", "-l", ROOT_RUNNER],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        problems.append(f"sudoers check failed for root runner: {exc}")
        return problems
    if completed.returncode != 0:
        reason = (completed.stderr or completed.stdout).strip()
        problems.append(f"sudoers does not allow root runner: {reason}")
    return problems
