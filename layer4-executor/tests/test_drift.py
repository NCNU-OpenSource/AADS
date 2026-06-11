"""
Drift detection tests (ADR-006).

Covers:
- plan_sha256: deterministic, key-order stable, content-sensitive
- classify_step_failure: all branches of the drift routing matrix
- validate_plan: accepts 3.0 and 3.1; rejects missing execution_profile in 3.1
- plan_auto_allowed: security_review_required overrides auto_execute
- recovery_start_index: step_failed_aborted returns step index for resume
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from executor import Executor


def _executor():
    return Executor()


# ---------------------------------------------------------------------------
# plan_sha256
# ---------------------------------------------------------------------------

class TestPlanSha256:

    def test_sha256_is_64_hex_chars(self):
        sha = Executor.plan_sha256({"schema_version": "3.1", "plan_id": "p1"})
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_sha256_is_deterministic(self):
        plan = {"schema_version": "3.1", "plan_id": "p1", "goal": "restore nginx"}
        assert Executor.plan_sha256(plan) == Executor.plan_sha256(plan)

    def test_sha256_stable_under_key_reordering(self):
        """Canonical JSON sort_keys=True means key order in Python dict is irrelevant."""
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert Executor.plan_sha256(p1) == Executor.plan_sha256(p2)

    def test_sha256_changes_on_content_change(self):
        base = {"schema_version": "3.1", "plan_id": "p1"}
        modified = {"schema_version": "3.1", "plan_id": "p1", "goal": "extra"}
        assert Executor.plan_sha256(base) != Executor.plan_sha256(modified)

    def test_sha256_of_3_0_and_3_1_differ(self):
        p30 = {"schema_version": "3.0", "plan_id": "p1"}
        p31 = {"schema_version": "3.1", "plan_id": "p1"}
        assert Executor.plan_sha256(p30) != Executor.plan_sha256(p31)


# ---------------------------------------------------------------------------
# classify_step_failure – drift routing matrix
# ---------------------------------------------------------------------------

class TestClassifyStepFailure:

    def test_hook_denied_pauses_as_policy_violation(self):
        action, drift_type = Executor.classify_step_failure(
            "step_failed_blocked", {"reason": "hook_denied"}
        )
        assert action == "pause"
        assert drift_type == "policy_violation"

    def test_node_locked_is_terminal_blocked_not_drift(self):
        action, drift_type = Executor.classify_step_failure(
            "step_failed_blocked", {"reason": "node_locked"}
        )
        assert action == "terminal_blocked"
        assert drift_type is None

    def test_blocked_unknown_reason_is_terminal_blocked(self):
        action, drift_type = Executor.classify_step_failure(
            "step_failed_blocked", {}
        )
        assert action == "terminal_blocked"
        assert drift_type is None

    def test_verification_failure_pauses(self):
        action, drift_type = Executor.classify_step_failure(
            "step_failed_aborted",
            {"verification": {"mismatches": [{"field": "active", "expected": True, "observed": False}]}}
        )
        assert action == "pause"
        assert drift_type == "verification_failed"

    def test_retries_exhausted_pauses(self):
        action, drift_type = Executor.classify_step_failure(
            "step_failed_aborted", {"error": "max attempts reached"}
        )
        assert action == "pause"
        assert drift_type == "step_retries_exhausted"

    def test_unknown_status_is_terminal_failed(self):
        action, drift_type = Executor.classify_step_failure(
            "something_unexpected", {}
        )
        assert action == "terminal_failed"
        assert drift_type is None


# ---------------------------------------------------------------------------
# validate_plan – dual schema support
# ---------------------------------------------------------------------------

def _base_runner(argv, side_effect="read", as_root=False):
    return {"mode": "argv", "argv": argv, "side_effect": side_effect,
            "as_root": as_root, "timeout_seconds": 10}


def _verification(argv, extract, expected):
    return {"type": "runner_probe", "runner": _base_runner(argv),
            "extract": extract, "expected": expected}


def _plan_30():
    return {
        "schema_version": "3.0",
        "plan_id": "p1", "rca_report_id": "r1", "target_node_id": "t1",
        "goal": "restore nginx", "risk_level": "low",
        "environment_policy": {"environment": "test", "auto_execute_allowed": True},
        "pre_execution_snapshot": {
            "enabled": True,
            "runner": _base_runner(["/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"], "mutate", True),
            "scope": "nginx_config",
        },
        "rollback": {"enabled": True,
                     "runner": _base_runner(["/usr/local/sbin/aads-nginx-restore-known-good"], "mutate", True)},
        "steps": [{
            "step_id": 1, "order": 1,
            "runner": _base_runner(["/usr/local/sbin/aads-nginx-start"], "mutate", True),
            "context": {"service": "nginx", "operation": "start"},
            "idempotency": {"mode": "idempotent", "max_attempts": 2},
            "expected_outcome": "nginx active", "on_failure": "rollback",
            "verification": _verification(
                ["systemctl", "is-active", "nginx"],
                {"active": {"from": "stdout_stripped", "equals": "active"}},
                {"active": True},
            ),
        }],
        "final_verification": _verification(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1/"],
            {"http_code": {"from": "stdout_stripped", "as_int": True}},
            {"http_code": 200},
        ),
        "self_check": {"passed": True},
    }


def _plan_31():
    p = _plan_30()
    p["schema_version"] = "3.1"
    p["execution_profile"] = {
        "profile_version": "1.0",
        "generated_by": "runner_catalog",
        "allowed_commands": [
            {"argv0": "/usr/local/sbin/aads-nginx-start", "argv_prefix": [],
             "as_root": True, "side_effect": "mutate", "max_timeout_seconds": 60},
        ],
        "path_permissions": [],
    }
    return p


class TestValidatePlan:

    def test_accepts_schema_30(self):
        assert _executor().validate_plan(_plan_30(), "3.0") is None

    def test_accepts_schema_31_with_profile(self):
        assert _executor().validate_plan(_plan_31(), "3.1") is None

    def test_31_requires_execution_profile(self):
        plan = _plan_31()
        del plan["execution_profile"]
        result = _executor().validate_plan(plan, "3.1")
        assert result == "missing_execution_profile"

    def test_31_requires_allowed_commands_non_empty(self):
        plan = _plan_31()
        plan["execution_profile"]["allowed_commands"] = []
        result = _executor().validate_plan(plan, "3.1")
        assert result == "missing_execution_profile"

    def test_rejects_schema_20(self):
        assert _executor().validate_plan({"schema_version": "2.0"}, "2.0") == "unsupported_schema"

    def test_rejects_unknown_schema(self):
        assert _executor().validate_plan({"schema_version": "9.9"}, "9.9") == "unsupported_schema"


# ---------------------------------------------------------------------------
# plan_auto_allowed
# ---------------------------------------------------------------------------

class TestPlanAutoAllowed:

    def test_low_risk_test_env_auto_execute_is_allowed(self):
        plan = _plan_30()
        assert _executor().plan_auto_allowed(plan) is True

    def test_security_review_required_blocks_auto(self):
        plan = _plan_30()
        plan["environment_policy"]["security_review_required"] = True
        assert _executor().plan_auto_allowed(plan) is False

    def test_non_test_env_blocks_auto(self):
        plan = _plan_30()
        plan["environment_policy"]["environment"] = "prod"
        assert _executor().plan_auto_allowed(plan) is False

    def test_high_risk_blocks_auto(self):
        plan = _plan_30()
        plan["risk_level"] = "high"
        assert _executor().plan_auto_allowed(plan) is False

    def test_auto_execute_false_blocks_auto(self):
        plan = _plan_30()
        plan["environment_policy"]["auto_execute_allowed"] = False
        assert _executor().plan_auto_allowed(plan) is False

    def test_31_with_security_review_required_blocks_auto(self):
        plan = _plan_31()
        plan["environment_policy"]["security_review_required"] = True
        assert _executor().plan_auto_allowed(plan) is False
