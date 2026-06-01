"""
Test ClaudeStylePlan schema validation and Agent output format

Validates that the new schema works correctly and Agent generates
proper TODO List format.
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
    FixingPlan,
    FixingPlanStep,
    PlanSelfCheck,
    RootCauseReport,
    StepCommand,
    VerificationSpec,
)


class TestStepCommand:
    """Test StepCommand schema"""

    def test_valid_step_command(self):
        """Valid StepCommand should be created"""
        cmd = StepCommand(
            tool_name="query_loki",
            target="nginx-container",
            command='query: {container="nginx"} | logfmt'
        )
        assert cmd.tool_name == "query_loki"
        assert cmd.target == "nginx-container"
        assert 'logfmt' in cmd.command
        assert cmd.schema_version == "1.0"
        assert cmd.risk_level == "low"

    def test_step_command_requires_all_fields(self):
        """Missing required fields should raise ValidationError"""
        with pytest.raises(ValidationError):
            StepCommand(tool_name="query_loki")  # Missing target and command


class TestFixingPlan:
    """Test executable FixingPlan v2 schema."""

    def test_valid_fixing_plan_v2(self):
        plan = FixingPlan(
            plan_id="diag_1",
            rca_report_id="rca_1",
            target_node_id="target-1",
            goal="Restore nginx",
            risk_level="low",
            environment_policy={"environment": "test", "auto_execute_allowed": True},
            steps=[
                FixingPlanStep(
                    step_id=1,
                    order=1,
                    command_id="nginx.start",
                    args={},
                    expected_outcome="nginx service is active",
                    on_failure="rollback",
                    verification=VerificationSpec(
                        command_id="nginx.status",
                        expected={"status": "success", "active": True},
                    ),
                )
            ],
            final_verification=VerificationSpec(
                command_id="nginx.http_check",
                args={"url": "http://127.0.0.1/", "expected_status": 200},
                expected={"status": "success", "http_status": 200},
            ),
            self_check=PlanSelfCheck(passed=True, rationale="catalog only"),
        )
        assert plan.schema_version == "2.0"
        assert plan.steps[0].order == 1

    def test_free_text_verification_rejected(self):
        with pytest.raises(ValidationError):
            VerificationSpec(command_id="nginx.status", expected={"text": "looks good"})

    def test_continue_failure_policy_rejected_for_v1(self):
        with pytest.raises(ValidationError):
            FixingPlanStep(
                step_id=1,
                order=1,
                command_id="nginx.start",
                args={},
                expected_outcome="nginx service is active",
                on_failure="continue",
                verification=VerificationSpec(
                    command_id="nginx.status",
                    expected={"status": "success", "active": True},
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


class TestExecutionStep:
    """Test ExecutionStep schema"""

    def test_valid_execution_step_with_commands(self):
        """Valid ExecutionStep with commands should be created"""
        step = ExecutionStep(
            step_id=1,
            title="查詢 nginx 錯誤日誌",
            phase="Explore",
            explanation="確認錯誤日誌的頻率和模式",
            requires_approval=False,
            commands=[
                StepCommand(
                    tool_name="query_loki",
                    target="nginx",
                    command='{container="nginx"} |= "error"'
                )
            ]
        )
        assert step.step_id == 1
        assert step.phase == "Explore"
        assert step.requires_approval is False
        assert len(step.commands) == 1
        assert step.status == "pending"  # Default value

    def test_execution_step_backward_compatibility(self):
        """Backward compatibility fields should have defaults"""
        step = ExecutionStep(
            step_id=1,
            title="Test",
            phase="Execute",
            explanation="Test explanation",
            commands=[]
        )
        assert step.action_type == "query"  # Default
        assert step.target == ""
        assert step.command == ""
        assert step.is_destructive is False

    def test_phase_validation(self):
        """Invalid phase should raise ValidationError"""
        with pytest.raises(ValidationError):
            ExecutionStep(
                step_id=1,
                title="Test",
                phase="InvalidPhase",  # Not in Literal["Explore", "Execute", "Verify"]
                explanation="Test",
                commands=[]
            )

    def test_status_validation(self):
        """Invalid status should raise ValidationError"""
        with pytest.raises(ValidationError):
            ExecutionStep(
                step_id=1,
                title="Test",
                phase="Explore",
                explanation="Test",
                status="invalid_status",  # Not in allowed statuses
                commands=[]
            )


class TestClaudeStylePlan:
    """Test ClaudeStylePlan schema"""

    def test_valid_claude_style_plan(self):
        """Valid ClaudeStylePlan should be created"""
        plan = ClaudeStylePlan(
            goal="重啟 nginx 容器以恢復服務",
            context_analysis="nginx 容器因 OOM 崩潰，需要重啟",
            proposed_approach="先查詢日誌確認問題，重啟容器，驗證恢復",
            execution_steps=[
                ExecutionStep(
                    step_id=1,
                    title="查詢 nginx 日誌",
                    phase="Explore",
                    explanation="確認 OOM 錯誤",
                    commands=[
                        StepCommand(
                            tool_name="query_loki",
                            target="nginx",
                            command='{container="nginx"} |= "OOM"'
                        )
                    ]
                ),
                ExecutionStep(
                    step_id=2,
                    title="重啟 nginx 容器",
                    phase="Execute",
                    explanation="恢復服務",
                    requires_approval=True,
                    commands=[
                        StepCommand(
                            tool_name="k8s_exec",
                            target="nginx",
                            command="kubectl rollout restart deployment/nginx"
                        )
                    ]
                ),
                ExecutionStep(
                    step_id=3,
                    title="驗證服務健康",
                    phase="Verify",
                    explanation="確認重啟成功",
                    commands=[
                        StepCommand(
                            tool_name="bash",
                            target="nginx",
                            command="curl -f http://nginx/health"
                        )
                    ]
                )
            ]
        )
        assert plan.goal == "重啟 nginx 容器以恢復服務"
        assert len(plan.execution_steps) == 3
        assert plan.execution_steps[0].phase == "Explore"
        assert plan.execution_steps[1].phase == "Execute"
        assert plan.execution_steps[1].requires_approval is True
        assert plan.execution_steps[2].phase == "Verify"
        assert plan.schema_version == "1.0"

    def test_backward_compatibility_fields(self):
        """Backward compatibility fields should have defaults"""
        plan = ClaudeStylePlan(
            goal="Test",
            context_analysis="Test analysis",
            proposed_approach="Test approach",
            execution_steps=[]
        )
        assert plan.root_cause == ""
        assert plan.confidence_score == 0.0

    def test_serialization_to_dict(self):
        """Plan should serialize to dict correctly"""
        plan = ClaudeStylePlan(
            goal="Test goal",
            context_analysis="Test context",
            proposed_approach="Test approach",
            execution_steps=[
                ExecutionStep(
                    step_id=1,
                    title="Test step",
                    phase="Explore",
                    explanation="Test",
                    commands=[
                        StepCommand(
                            tool_name="query_loki",
                            target="test",
                            command="test query"
                        )
                    ]
                )
            ]
        )

        data = plan.model_dump()
        assert 'goal' in data
        assert 'execution_steps' in data
        assert len(data['execution_steps']) == 1
        assert 'commands' in data['execution_steps'][0]
        assert len(data['execution_steps'][0]['commands']) == 1
