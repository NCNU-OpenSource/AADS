"""
Test ClaudeStylePlan schema validation and FixingPlan 3.0 (runner-based) output.
"""
import pytest
from pydantic import ValidationError
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from schemas.action_plan import (
    ClaudeStylePlan,
    ExecutionStep,
    ExtractRule,
    FixingPlan,
    FixingPlanStep,
    PlanSelfCheck,
    PreExecutionSnapshot,
    RollbackSpec,
    RootCauseReport,
    RunnerSpec,
    StepCommand,
    VerificationSpec,
)


def _read_runner(argv):
    return RunnerSpec(argv=argv, side_effect="read")


def _mutate_runner(argv):
    return RunnerSpec(argv=argv, as_root=True, side_effect="mutate")


def _verification(argv, extract, expected):
    return VerificationSpec(
        runner=_read_runner(argv),
        extract={k: ExtractRule.model_validate(v) for k, v in extract.items()},
        expected=expected,
    )


class TestStepCommand:
    """Test StepCommand schema"""

    def test_valid_step_command(self):
        cmd = StepCommand(
            tool_name="query_loki",
            target="nginx-container",
            command='query: {container="nginx"} | logfmt'
        )
        assert cmd.tool_name == "query_loki"
        assert cmd.target == "nginx-container"
        assert 'logfmt' in cmd.command
        assert cmd.risk_level == "low"

    def test_node_agent_command_uses_operation(self):
        cmd = StepCommand(tool_name="node_agent", target="mysql", command="mysql.restart", operation="restart")
        assert cmd.operation == "restart"
        assert not hasattr(cmd, "command_id")

    def test_step_command_requires_all_fields(self):
        with pytest.raises(ValidationError):
            StepCommand(tool_name="query_loki")  # Missing target and command


class TestRunnerSpec:
    """Test RunnerSpec / ExtractRule / VerificationSpec building blocks."""

    def test_runner_requires_non_empty_argv(self):
        with pytest.raises(ValidationError):
            RunnerSpec(argv=[])

    def test_extract_rule_from_alias(self):
        rule = ExtractRule.model_validate({"from": "stdout_stripped", "equals": "active"})
        assert rule.from_ == "stdout_stripped"
        # round-trips to wire key `from`
        assert rule.model_dump(by_alias=True)["from"] == "stdout_stripped"

    def test_verification_runner_must_be_read(self):
        with pytest.raises(ValidationError):
            VerificationSpec(
                runner=_mutate_runner(["/x"]),
                extract={"returncode": ExtractRule.model_validate({"from": "returncode"})},
                expected={"returncode": 0},
            )

    def test_verification_extract_must_cover_expected(self):
        with pytest.raises(ValidationError):
            VerificationSpec(
                runner=_read_runner(["systemctl", "is-active", "nginx"]),
                extract={},
                expected={"active": True},
            )


class TestFixingPlan:
    """Test executable FixingPlan 3.0 schema."""

    def test_valid_fixing_plan_v3(self):
        plan = FixingPlan(
            plan_id="diag_1",
            rca_report_id="rca_1",
            target_node_id="target-1",
            goal="Restore nginx",
            risk_level="low",
            environment_policy={"environment": "test", "auto_execute_allowed": True},
            pre_execution_snapshot=PreExecutionSnapshot(
                enabled=True,
                runner=_mutate_runner(["/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"]),
                scope="nginx_config",
            ),
            rollback=RollbackSpec(enabled=True, runner=_mutate_runner(["/usr/local/sbin/aads-nginx-restore-known-good"])),
            steps=[
                FixingPlanStep(
                    step_id=1,
                    order=1,
                    runner=_mutate_runner(["/usr/local/sbin/aads-nginx-start"]),
                    context={"service": "nginx", "operation": "start"},
                    expected_outcome="nginx service is active",
                    on_failure="rollback",
                    verification=_verification(
                        ["systemctl", "is-active", "nginx"],
                        {"active": {"from": "stdout_stripped", "equals": "active"}},
                        {"active": True},
                    ),
                )
            ],
            final_verification=_verification(
                ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1/"],
                {"http_code": {"from": "stdout_stripped", "as_int": True}},
                {"http_code": 200},
            ),
            self_check=PlanSelfCheck(passed=True, rationale="runner specs"),
        )
        assert plan.schema_version == "3.0"
        assert plan.steps[0].runner.side_effect == "mutate"
        assert plan.steps[0].runner.argv[0].endswith("aads-nginx-start")

    def test_free_text_verification_rejected(self):
        with pytest.raises(ValidationError):
            VerificationSpec(
                runner=_read_runner(["systemctl", "is-active", "nginx"]),
                extract={"text": ExtractRule.model_validate({"from": "stdout"})},
                expected={"text": "looks good"},
            )

    def test_invalid_failure_policy_rejected(self):
        with pytest.raises(ValidationError):
            FixingPlanStep(
                step_id=1,
                order=1,
                runner=_mutate_runner(["/usr/local/sbin/aads-nginx-start"]),
                expected_outcome="nginx service is active",
                on_failure="continue",
                verification=_verification(
                    ["systemctl", "is-active", "nginx"],
                    {"active": {"from": "stdout_stripped", "equals": "active"}},
                    {"active": True},
                ),
            )

    def test_root_cause_report_schema(self):
        report = RootCauseReport(
            report_id="rca_1",
            target_node_id="target-1",
            affected_service="nginx",
            root_cause="nginx stopped",
            confidence=0.9,
            recommended_capabilities=["nginx.start"],
        )
        assert report.affected_service == "nginx"

    @pytest.mark.parametrize(
        ("argv", "scope"),
        [
            (["/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"], "nginx_config"),
            (["/usr/local/sbin/aads-postgresql-ensure-config-snapshot"], "postgresql_config"),
            (["/usr/local/sbin/aads-redis-ensure-config-snapshot"], "redis_config"),
            (["/usr/local/sbin/aads-mysql-ensure-config-snapshot"], "mysql_config"),
        ],
    )
    def test_pre_execution_snapshot_allows_service_config_scopes(self, argv, scope):
        snapshot = PreExecutionSnapshot(enabled=True, runner=_mutate_runner(argv), scope=scope)
        assert snapshot.runner.argv == argv
        assert snapshot.scope == scope


class TestExecutionStep:
    """Test ExecutionStep schema"""

    def test_valid_execution_step_with_commands(self):
        step = ExecutionStep(
            step_id=1,
            title="查詢 nginx 錯誤日誌",
            phase="Explore",
            explanation="確認錯誤日誌的頻率和模式",
            requires_approval=False,
            commands=[
                StepCommand(tool_name="query_loki", target="nginx", command='{container="nginx"} |= "error"')
            ]
        )
        assert step.step_id == 1
        assert step.phase == "Explore"
        assert step.status == "pending"

    def test_execution_step_backward_compatibility(self):
        step = ExecutionStep(step_id=1, title="Test", phase="Execute", explanation="Test explanation", commands=[])
        assert step.action_type == "query"
        assert step.target == ""
        assert step.is_destructive is False

    def test_phase_validation(self):
        with pytest.raises(ValidationError):
            ExecutionStep(step_id=1, title="Test", phase="InvalidPhase", explanation="Test", commands=[])


class TestClaudeStylePlan:
    """Test ClaudeStylePlan schema"""

    def test_valid_claude_style_plan(self):
        plan = ClaudeStylePlan(
            goal="重啟 nginx 容器以恢復服務",
            context_analysis="nginx 容器因 OOM 崩潰，需要重啟",
            proposed_approach="先查詢日誌確認問題，重啟容器，驗證恢復",
            execution_steps=[
                ExecutionStep(
                    step_id=1, title="查詢 nginx 日誌", phase="Explore", explanation="確認 OOM 錯誤",
                    commands=[StepCommand(tool_name="query_loki", target="nginx", command='{container="nginx"} |= "OOM"')],
                ),
                ExecutionStep(
                    step_id=2, title="重啟 nginx 服務", phase="Execute", explanation="恢復服務", requires_approval=True,
                    commands=[StepCommand(tool_name="node_agent", target="nginx", command="nginx.start", operation="start")],
                ),
            ],
        )
        assert len(plan.execution_steps) == 2
        assert plan.execution_steps[1].commands[0].operation == "start"
        assert plan.schema_version == "1.0"

    def test_serialization_to_dict(self):
        plan = ClaudeStylePlan(
            goal="Test goal",
            context_analysis="Test context",
            proposed_approach="Test approach",
            execution_steps=[
                ExecutionStep(
                    step_id=1, title="Test step", phase="Explore", explanation="Test",
                    commands=[StepCommand(tool_name="query_loki", target="test", command="test query")],
                )
            ],
        )
        data = plan.model_dump()
        assert 'execution_steps' in data
        assert len(data['execution_steps'][0]['commands']) == 1
