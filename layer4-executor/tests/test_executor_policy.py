import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from executor import Executor


def _runner(argv, side_effect="read", as_root=False):
    return {"mode": "argv", "argv": argv, "side_effect": side_effect, "as_root": as_root, "timeout_seconds": 10}


def _verification(argv, extract, expected):
    return {"type": "runner_probe", "runner": _runner(argv), "extract": extract, "expected": expected}


def _valid_plan():
    return {
        "schema_version": "3.0",
        "plan_id": "diag_1",
        "rca_report_id": "rca_1",
        "target_node_id": "target-1",
        "goal": "Restore nginx",
        "risk_level": "low",
        "environment_policy": {"environment": "test", "auto_execute_allowed": True},
        "pre_execution_snapshot": {
            "enabled": True,
            "runner": _runner(["/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"], "mutate", True),
            "scope": "nginx_config",
        },
        "rollback": {
            "enabled": True,
            "runner": _runner(["/usr/local/sbin/aads-nginx-restore-known-good"], "mutate", True),
        },
        "steps": [
            {
                "step_id": 1,
                "order": 1,
                "runner": _runner(["/usr/local/sbin/aads-nginx-start"], "mutate", True),
                "context": {"service": "nginx", "operation": "start"},
                "idempotency": {"mode": "idempotent", "max_attempts": 2},
                "expected_outcome": "nginx active",
                "on_failure": "rollback",
                "verification": _verification(
                    ["systemctl", "is-active", "nginx"],
                    {"active": {"from": "stdout_stripped", "equals": "active"}},
                    {"active": True},
                ),
            }
        ],
        "final_verification": _verification(
            ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1/"],
            {"http_code": {"from": "stdout_stripped", "as_int": True}},
            {"http_code": 200},
        ),
        "self_check": {"passed": True},
    }


def test_max_attempts_uses_idempotency_not_catalog():
    executor = Executor()
    assert executor.max_attempts({"mode": "idempotent", "max_attempts": 2}) == 2
    assert executor.max_attempts({"mode": "non_idempotent", "max_attempts": 3}) == 1
    assert executor.max_attempts({}) == 2
    assert executor.max_attempts({"mode": "idempotent", "max_attempts": 5}) == 5


def test_environment_policy_blocks_mismatch():
    executor = Executor()
    assert executor.environment_allowed("test", {"environment": "test"})
    assert not executor.environment_allowed("prod", {"environment": "test"})


def test_validation_rejects_legacy_v2_schema():
    executor = Executor()
    assert executor.validate_plan({"schema_version": "2.0"}, "2.0") == "unsupported_schema"


def test_validation_accepts_runner_plan():
    executor = Executor()
    assert executor.validate_plan(_valid_plan(), "3.0") is None


def test_validation_rejects_missing_runner_argv():
    executor = Executor()
    plan = _valid_plan()
    plan["steps"][0]["runner"]["argv"] = []
    assert executor.validate_plan(plan, "3.0") == "invalid_runner_argv"


def test_validation_rejects_mutating_verification_runner():
    executor = Executor()
    plan = _valid_plan()
    plan["steps"][0]["verification"]["runner"]["side_effect"] = "mutate"
    assert executor.validate_plan(plan, "3.0") == "verification_runner_not_read"


def test_free_text_verification_is_rejected():
    executor = Executor()
    plan = _valid_plan()
    plan["steps"][0]["verification"]["expected"] = {"text": "ok"}
    assert executor.validate_plan(plan, "3.0") == "free_text_verification_not_allowed"


def test_extract_must_cover_expected_keys():
    executor = Executor()
    plan = _valid_plan()
    plan["steps"][0]["verification"]["extract"] = {}
    assert executor.validate_plan(plan, "3.0") == "extract_missing_expected_keys"


def test_invalid_on_failure_rejected():
    executor = Executor()
    plan = _valid_plan()
    plan["steps"][0]["on_failure"] = "continue"
    assert executor.validate_plan(plan, "3.0") == "invalid_on_failure"


def test_apply_extractors_produces_structured_fields():
    executor = Executor()
    # is-active -> active
    observed = executor.apply_extractors(
        {"returncode": 0, "stdout": "active\n", "stderr": ""},
        {"active": {"from": "stdout_stripped", "equals": "active"}},
    )
    assert observed == {"active": True}
    # curl http_code
    observed = executor.apply_extractors(
        {"returncode": 0, "stdout": "200", "stderr": ""},
        {"http_code": {"from": "stdout_stripped", "as_int": True}},
    )
    assert observed == {"http_code": 200}
    # redis ping
    observed = executor.apply_extractors(
        {"returncode": 0, "stdout": "PONG\n", "stderr": ""},
        {"pong": {"from": "stdout_stripped", "equals": "PONG"}},
    )
    assert observed == {"pong": True}
    # returncode raw
    observed = executor.apply_extractors(
        {"returncode": 0, "stdout": "", "stderr": ""},
        {"returncode": {"from": "returncode"}},
    )
    assert observed == {"returncode": 0}


def test_broken_service_is_not_falsely_verified():
    executor = Executor()
    # nginx is down: is-active returns "inactive" with rc 3
    observed = executor.apply_extractors(
        {"returncode": 3, "stdout": "inactive\n", "stderr": ""},
        {"active": {"from": "stdout_stripped", "equals": "active"}},
    )
    match, mismatches = executor.matches_expected(observed, {"active": True})
    assert match is False
    assert mismatches[0]["field"] == "active"
    assert mismatches[0]["observed"] is False


def test_structured_expected_matching():
    executor = Executor()
    ok, mismatches = executor.matches_expected({"http_code": 200}, {"http_code": 200})
    assert ok and mismatches == []
    ok, mismatches = executor.matches_expected({"http_code": 502}, {"http_code": 200})
    assert not ok and mismatches[0]["field"] == "http_code"


def test_step_label_from_context():
    executor = Executor()
    step = {"context": {"service": "mysql", "operation": "restore_config"}, "runner": {"argv": ["/x"]}}
    assert executor.step_label(step) == "mysql.restore_config"
    step = {"context": {}, "runner": {"argv": ["/usr/local/sbin/aads-nginx-start"]}}
    assert executor.step_label(step) == "/usr/local/sbin/aads-nginx-start"


def test_knowledge_agent_has_no_llm_client_imports():
    with open(os.path.join(os.path.dirname(__file__), "..", "src", "executor.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "ChatOpenAI" not in source
    assert "OpenAI" not in source
    assert "LiteLLM" not in source
