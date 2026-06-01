import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from executor import Executor


def test_mutating_retry_requires_idempotent_catalog_flag():
    executor = Executor()
    assert executor.max_attempts(True, {"idempotent": True}) == 2
    assert executor.max_attempts(True, {"idempotent": False}) == 1
    assert executor.max_attempts(False, {"idempotent": False}) == 3


def test_find_command_requires_matching_schema_version():
    executor = Executor()
    facts = {
        "supported_commands": [
            {"command_id": "nginx.start", "schema_version": "1.0"},
        ]
    }
    assert executor.find_command(facts, "nginx.start", "1.0")
    assert executor.find_command(facts, "nginx.start", "2.0") is None


def test_environment_policy_blocks_mismatch():
    executor = Executor()
    command_policy = {"environment": "test"}
    assert executor.environment_allowed("test", command_policy)
    assert not executor.environment_allowed("prod", command_policy)


def test_fixing_plan_v2_validation_rejects_legacy_schema():
    executor = Executor()
    assert executor.validate_plan({"schema_version": "1.0"}, "1.0") == "unsupported_schema"


def test_free_text_verification_is_rejected():
    executor = Executor()
    plan = {
        "schema_version": "2.0",
        "plan_id": "diag_1",
        "rca_report_id": "rca_1",
        "target_node_id": "target-1",
        "goal": "Restore nginx",
        "risk_level": "low",
        "environment_policy": {"environment": "test", "auto_execute_allowed": True},
        "pre_execution_snapshot": {"enabled": True, "command_id": "nginx.ensure_known_good_snapshot"},
        "steps": [
            {
                "step_id": 1,
                "order": 1,
                "command_id": "nginx.start",
                "args": {},
                "expected_outcome": "nginx active",
                "on_failure": "rollback",
                "verification": {"type": "catalog_probe", "command_id": "nginx.status", "expected": {"text": "ok"}},
            }
        ],
        "final_verification": {"type": "catalog_probe", "command_id": "nginx.http_check", "expected": {"status": "success", "http_status": 200}},
        "self_check": {"passed": True},
    }
    assert executor.validate_plan(plan, "2.0") == "free_text_verification_not_allowed"


def test_structured_expected_matching():
    executor = Executor()
    ok, mismatches = executor.matches_expected({"status": "success", "active": True}, {"status": "success", "active": True})
    assert ok
    assert mismatches == []
    ok, mismatches = executor.matches_expected({"status": "success", "active": False}, {"status": "success", "active": True})
    assert not ok
    assert mismatches[0]["field"] == "active"


def test_knowledge_agent_has_no_llm_client_imports():
    with open(os.path.join(os.path.dirname(__file__), "..", "src", "executor.py"), encoding="utf-8") as fh:
        source = fh.read()
    assert "ChatOpenAI" not in source
    assert "OpenAI" not in source
    assert "LiteLLM" not in source
