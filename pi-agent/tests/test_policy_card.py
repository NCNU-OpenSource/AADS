"""
PolicyCard enforcement tests (ADR-005).

Covers:
- profile_missing → deny (enforce) / warn (audit)
- argv0 + prefix matching (exact, prefix-only, extra-args rejected)
- as_root, side_effect, timeout mismatches
- cwd '..' rejection; path-arg '/' token coverage; env rejection
- Full /v1/commands/run integration: allow, deny-with-failed_check, audit warn
- AuditHook still fires after PolicyCard (last hook wins for display, but PolicyCard
  verdict is preserved when it denies)
- profile_sha256 is stable and present in every decision annotation
"""
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Load safety_cards as a package from the src tree
# ---------------------------------------------------------------------------
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from safety_cards.policy_card import (
    ExecutionProfile,
    CommandPermission,
    PathPermission,
    HookDecision,
    PolicyCard,
    profile_allows,
    profile_sha256,
    POLICY_MODE,
)

# Also load the full pi-agent app for integration tests
SRC_MAIN = SRC / "main.py"
spec = importlib.util.spec_from_file_location("pi_agent_main", SRC_MAIN)
pi_agent_main = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
assert spec and spec.loader
spec.loader.exec_module(pi_agent_main)  # type: ignore[union-attr]

from fastapi.testclient import TestClient

TEST_TOKEN = "test-token"
CARD_ID = "policy.execution_profile.v1"


def _client(monkeypatch):
    monkeypatch.setattr(pi_agent_main, "TOKEN", TEST_TOKEN)
    return TestClient(pi_agent_main.app)


def _auth():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


# ---------------------------------------------------------------------------
# Minimal profile + runner helpers
# ---------------------------------------------------------------------------

def _profile(argv0: str, prefix: List[str] = [], as_root: bool = False,
             side_effect: str = "read", allow_extra_args: bool = False,
             paths: Optional[List[Dict]] = None) -> Dict[str, Any]:
    cmd: Dict[str, Any] = {
        "argv0": argv0, "argv_prefix": prefix,
        "as_root": as_root, "side_effect": side_effect,
        "allow_extra_args": allow_extra_args, "max_timeout_seconds": 60,
    }
    return {
        "profile_version": "1.0",
        "generated_by": "runner_catalog",
        "allowed_commands": [cmd],
        "path_permissions": paths or [],
    }


def _runner(argv: List[str], as_root: bool = False, side_effect: str = "read",
            timeout: int = 10, cwd: str = "/", env: Optional[Dict] = None) -> Dict[str, Any]:
    r: Dict[str, Any] = {"argv": argv, "as_root": as_root, "side_effect": side_effect,
                          "timeout_seconds": timeout, "cwd": cwd}
    if env:
        r["env"] = env
    return r


# ---------------------------------------------------------------------------
# profile_allows – unit tests
# ---------------------------------------------------------------------------

class TestProfileAllows:

    def test_none_profile_returns_profile_missing(self):
        assert profile_allows(None, _runner(["systemctl", "is-active", "nginx"])) == "profile_missing"

    def test_empty_allowed_commands_returns_profile_missing(self):
        p = {"profile_version": "1.0", "generated_by": "runner_catalog",
             "allowed_commands": [], "path_permissions": []}
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx"])) == "profile_missing"

    def test_exact_match_is_allowed(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx"])) is None

    def test_wrong_argv0_denied(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        assert profile_allows(p, _runner(["service", "nginx", "status"])) == "argv_not_allowed"

    def test_extra_args_rejected_when_allow_extra_args_false(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"], allow_extra_args=False)
        # Extra "--quiet" beyond the prefix
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx", "--quiet"])) == "argv_not_allowed"

    def test_extra_args_allowed_when_flag_set(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"], allow_extra_args=True)
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx", "--quiet"])) is None

    def test_as_root_mismatch_denied(self):
        p = _profile("/usr/local/sbin/aads-nginx-start", as_root=True, side_effect="mutate")
        # runner says as_root=False
        assert profile_allows(p, _runner(["/usr/local/sbin/aads-nginx-start"],
                                          as_root=False, side_effect="mutate")) == "as_root_mismatch"

    def test_side_effect_mismatch_denied(self):
        p = _profile("/usr/local/sbin/aads-nginx-start", as_root=True, side_effect="mutate")
        assert profile_allows(p, _runner(["/usr/local/sbin/aads-nginx-start"],
                                          as_root=True, side_effect="read")) == "side_effect_mismatch"

    def test_timeout_exceeds_profile_denied(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx"],
                                          timeout=120)) == "timeout_exceeds_profile"

    def test_cwd_dotdot_denied(self):
        p = _profile("ls", paths=[{"path": "/etc/nginx", "mode": "r"}])
        assert profile_allows(p, _runner(["ls"], cwd="/etc/nginx/../..")) == "cwd_not_allowed"

    def test_cwd_slash_is_free_pass(self):
        """cwd='/' is the RunnerSpec default — a free pass that grants no data access."""
        p = _profile("ls")
        assert profile_allows(p, _runner(["ls"], cwd="/")) is None

    def test_cwd_not_covered_by_paths(self):
        p = _profile("ls", paths=[{"path": "/var/lib/aads-agent/snapshots/nginx", "mode": "r"}])
        assert profile_allows(p, _runner(["ls"], cwd="/etc/nginx")) == "cwd_not_allowed"

    def test_path_arg_covered_by_permission(self):
        # cat takes arbitrary file args → allow_extra_args=True reflects real catalog entry
        p = _profile("cat", allow_extra_args=True,
                     paths=[{"path": "/etc/nginx", "mode": "r"}])
        assert profile_allows(p, _runner(["cat", "/etc/nginx/nginx.conf"])) is None

    def test_path_arg_dotdot_denied(self):
        p = _profile("cat", allow_extra_args=True, paths=[{"path": "/etc/nginx", "mode": "r"}])
        assert profile_allows(p, _runner(["cat", "/etc/nginx/../passwd"])) == "path_arg_not_allowed"

    def test_path_arg_outside_permission_denied(self):
        p = _profile("cat", allow_extra_args=True, paths=[{"path": "/etc/nginx", "mode": "r"}])
        assert profile_allows(p, _runner(["cat", "/etc/shadow"])) == "path_arg_not_allowed"

    def test_non_path_arg_not_checked(self):
        """Relative or plain args (like service names) are not subject to path checking."""
        # Full argv pinned: ["systemctl", "is-active", "nginx"]
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        assert profile_allows(p, _runner(["systemctl", "is-active", "nginx"])) is None

    def test_env_not_allowed(self):
        p = _profile("env")
        assert profile_allows(p, _runner(["env"], env={"MY_VAR": "1"})) == "env_not_allowed"


class TestProfileSha256:

    def test_sha_is_64_hex_chars(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        sha = profile_sha256(p)
        assert sha and len(sha) == 64

    def test_sha_stable_across_calls(self):
        p = _profile("systemctl", prefix=["is-active", "nginx"])
        assert profile_sha256(p) == profile_sha256(p)

    def test_sha_changes_on_content_change(self):
        p1 = _profile("systemctl", prefix=["is-active", "nginx"])
        p2 = _profile("systemctl", prefix=["is-active", "redis"])
        assert profile_sha256(p1) != profile_sha256(p2)

    def test_none_profile_returns_none(self):
        assert profile_sha256(None) is None


# ---------------------------------------------------------------------------
# PolicyCard integration tests via /v1/commands/run
# ---------------------------------------------------------------------------

class TestPolicyCardEnforce:
    """AADS_POLICY_MODE=enforce (default) — violations must deny."""

    def test_enforce_is_default_mode(self):
        assert POLICY_MODE == "enforce"

    def test_matching_profile_allows_execution(self, monkeypatch):
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={
                "runner": {"argv": ["echo", "hello"]},
                "context": {},
                "execution_profile": {
                    "allowed_commands": [{"argv0": "echo", "argv_prefix": ["hello"]}],
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        # When PolicyCard allows, AuditHook is the surfaced result (last hook wins on "allow").
        # The execution status confirms PolicyCard did NOT deny.
        assert body["hook"]["decision"] == "allow"
        assert body["hook"]["argv_sha256"]  # always present via _hook_payload

    def test_missing_profile_is_denied(self, monkeypatch):
        """No execution_profile in request → profile_missing → deny."""
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={"runner": {"argv": ["ls", "/etc"]}, "context": {}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "blocked"
        assert body["hook"]["decision"] == "deny"
        assert body["hook"]["annotations"]["failed_check"] == "profile_missing"

    def test_argv_not_in_profile_is_denied(self, monkeypatch):
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={
                "runner": {"argv": ["rm", "-rf", "/"]},
                "context": {},
                "execution_profile": {
                    "allowed_commands": [{"argv0": "ls"}],
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "blocked"
        assert body["hook"]["annotations"]["failed_check"] == "argv_not_allowed"

    def test_failed_check_in_deny_annotations(self, monkeypatch):
        """Annotations must always carry failed_check on denial for audit trails."""
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={
                "runner": {"argv": ["systemctl", "stop", "nginx"], "as_root": False},
                "context": {},
                "execution_profile": {
                    "allowed_commands": [{"argv0": "systemctl", "argv_prefix": ["is-active", "nginx"]}],
                },
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "blocked"
        annotations = body["hook"]["annotations"]
        assert "failed_check" in annotations
        assert annotations["failed_check"] == "argv_not_allowed"
        assert annotations["argv0"] == "systemctl"


class TestPolicyCardAuditMode:
    """AADS_POLICY_MODE=audit — violations warn but do NOT block execution."""

    def test_audit_mode_warns_not_denies(self, monkeypatch):
        import safety_cards.policy_card as pc_module
        monkeypatch.setattr(pc_module, "POLICY_MODE", "audit")
        # Patch the card on the global HOOKS list too
        monkeypatch.setattr(pi_agent_main, "HOOKS", [pc_module.PolicyCard(), pi_agent_main.AuditHook()])
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={"runner": {"argv": ["ls", "/tmp"]}, "context": {}},
            # No execution_profile — in enforce this denies; in audit should warn
        )
        assert resp.status_code == 200
        body = resp.json()
        # Execution proceeded (warn does not block)
        assert body["status"] == "success"
        assert body["hook"]["decision"] == "warn"
        assert body["hook"]["card_id"] == CARD_ID

    def test_audit_mode_annotation_still_has_failed_check(self, monkeypatch):
        import safety_cards.policy_card as pc_module
        monkeypatch.setattr(pc_module, "POLICY_MODE", "audit")
        monkeypatch.setattr(pi_agent_main, "HOOKS", [pc_module.PolicyCard(), pi_agent_main.AuditHook()])
        resp = _client(monkeypatch).post(
            "/v1/commands/run",
            headers=_auth(),
            json={"runner": {"argv": ["ls"]}, "context": {}},
        )
        body = resp.json()
        assert body["hook"]["annotations"].get("failed_check") == "profile_missing"
