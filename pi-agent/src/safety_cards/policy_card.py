"""
PolicyCard: ExecutionProfile enforcement on the target node (ADR-005).

The ExecutionProfile is the AppArmor-like permission manifest generated
deterministically at plan time (layer2 runner_catalog), approved by the human
together with the plan, and shipped with every command request. This card is
the framework-side enforcement point: a runner spec outside the approved
command/path scope is denied before execution, regardless of what any LLM or
upstream component asked for.

``profile_allows`` is a verbatim twin of
``layer2-analyzer/src/schemas/action_plan.py::profile_allows`` — the agent must
not import layer2, and a parity test pins the two copies together.

Modes (AADS_POLICY_MODE):
- ``enforce`` (default): violations return ``deny`` and the command never runs.
- ``audit``: violations return ``warn`` with full annotations — lab escape
  hatch for legacy 3.0 plans that carry no profile.
"""
import hashlib
import json
import os
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

POLICY_MODE = os.getenv("AADS_POLICY_MODE", "enforce")


class HookDecision(BaseModel):
    """Hook pipeline verdict contract (shared by all cards and the AuditHook)."""

    decision: str  # allow | deny | warn
    card_id: str
    reason: str = ""
    annotations: Dict[str, Any] = Field(default_factory=dict)


class CommandPermission(BaseModel):
    argv0: str = Field(..., min_length=1)
    argv_prefix: List[str] = Field(default_factory=list)
    allow_extra_args: bool = Field(default=False)
    as_root: bool = Field(default=False)
    side_effect: Literal["read", "mutate"] = Field(default="read")
    max_timeout_seconds: int = Field(default=600, ge=1, le=600)


class PathPermission(BaseModel):
    path: str = Field(..., min_length=1, pattern=r"^/")
    mode: str = Field(..., pattern=r"^[rwx]{1,3}$")


class ExecutionProfile(BaseModel):
    profile_version: Literal["1.0"] = Field(default="1.0")
    generated_by: str = Field(default="runner_catalog")
    allowed_commands: List[CommandPermission] = Field(..., min_length=1)
    path_permissions: List[PathPermission] = Field(default_factory=list)


def profile_allows(profile: Dict[str, Any], runner: Dict[str, Any]) -> Optional[str]:
    """
    Pure matching predicate: does ``profile`` permit ``runner``?

    Returns None when allowed, otherwise a machine-readable failure reason.
    Verbatim twin of the layer2 implementation — keep both in sync.
    """
    if not profile or not profile.get("allowed_commands"):
        return "profile_missing"

    argv = list(runner.get("argv") or [])
    if not argv:
        return "argv_not_allowed"

    matched = None
    for perm in profile["allowed_commands"]:
        prefix = list(perm.get("argv_prefix") or [])
        if argv[0] != perm["argv0"] or argv[1:1 + len(prefix)] != prefix:
            continue
        if not perm.get("allow_extra_args", False) and len(argv) != 1 + len(prefix):
            continue
        matched = perm
        break
    if matched is None:
        return "argv_not_allowed"

    if bool(runner.get("as_root", False)) != bool(matched.get("as_root", False)):
        return "as_root_mismatch"
    if (runner.get("side_effect") or "read") != (matched.get("side_effect") or "read"):
        return "side_effect_mismatch"
    if int(runner.get("timeout_seconds") or 30) > int(matched.get("max_timeout_seconds") or 600):
        return "timeout_exceeds_profile"

    path_perms = profile.get("path_permissions") or []

    def _covered(target: str, need_mode: str) -> bool:
        normalized = os.path.normpath(target)
        for perm in path_perms:
            base = os.path.normpath(perm["path"])
            within = normalized == base or normalized.startswith(base.rstrip("/") + "/")
            if within and (not need_mode or need_mode in perm.get("mode", "")):
                return True
        return False

    # cwd "/" is the RunnerSpec default and grants no data access by itself;
    # any other cwd must be explicitly covered (a "/" path_permission would
    # cover every path argument and void the check, so it is never emitted).
    cwd = runner.get("cwd") or "/"
    if ".." in cwd.split("/"):
        return "cwd_not_allowed"
    if cwd != "/" and not _covered(cwd, "r"):
        return "cwd_not_allowed"

    for token in argv[1:]:
        if not token.startswith("/"):
            continue
        if ".." in token.split("/"):
            return "path_arg_not_allowed"
        if not _covered(token, ""):
            return "path_arg_not_allowed"

    env = runner.get("env") or {}
    if env:
        return "env_not_allowed"

    return None


def profile_sha256(profile: Optional[Dict[str, Any]]) -> Optional[str]:
    if not profile:
        return None
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PolicyCard:
    """
    Hook that enforces the request's ExecutionProfile before execution.

    Returns ``deny`` (enforce mode) or ``warn`` (audit mode) when the runner
    spec falls outside the profile; ``allow`` otherwise. Annotations always
    carry the failed check and the profile hash so the audit trail ties every
    decision to the exact approved scope.
    """

    card_id = "policy.execution_profile.v1"

    def before_run(self, request, argv_sha: str) -> HookDecision:
        profile = request.execution_profile.model_dump() if request.execution_profile else None
        reason = profile_allows(profile, request.runner.model_dump())
        annotations: Dict[str, Any] = {
            "policy_mode": POLICY_MODE,
            "profile_sha256": profile_sha256(profile),
            "argv_sha256": argv_sha,
            "argv0": request.runner.argv[0] if request.runner.argv else None,
        }
        if reason is None:
            return HookDecision(
                decision="allow",
                card_id=self.card_id,
                reason="runner spec within execution profile",
                annotations=annotations,
            )
        annotations["failed_check"] = reason
        if POLICY_MODE == "audit":
            return HookDecision(
                decision="warn",
                card_id=self.card_id,
                reason=f"policy violation (audit mode): {reason}",
                annotations=annotations,
            )
        return HookDecision(
            decision="deny",
            card_id=self.card_id,
            reason=f"policy violation: {reason}",
            annotations=annotations,
        )

    def after_run(self, request, result: Dict[str, Any]) -> HookDecision:
        return HookDecision(
            decision="allow", card_id=self.card_id, reason="post-run (policy evaluated before run)"
        )

    def on_error(self, request, error: str) -> HookDecision:
        return HookDecision(decision="allow", card_id=self.card_id, reason=f"error audit: {error}")
