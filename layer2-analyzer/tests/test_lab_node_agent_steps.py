import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from main import RootCauseAnalyzer
from schemas.action_plan import ClaudeStylePlan, ExecutionStep, RootCauseReport, StepCommand


def _analyzer():
    analyzer = RootCauseAnalyzer.__new__(RootCauseAnalyzer)
    analyzer.default_node_id = "target-1"
    analyzer.default_node_environment = "test"
    analyzer.force_gate_approval = True
    return analyzer


def _operations(plan):
    return [
        (command.target, command.operation)
        for step in plan.execution_steps
        if step.phase == "Execute"
        for command in step.commands
    ]


def _mysql_bad_config_cluster():
    return SimpleNamespace(
        anomalies=[
            {
                "raw_message": (
                    "mysql-systemd-start: MySQL data dir not found at "
                    "/nonexistent/aads-demo-mysql-bad-config"
                ),
                "service": "mysql",
            },
            {
                "raw_message": "mysql config error: invalid datadir from AADS manual demo",
                "service": "mysql",
            },
        ],
        containers=[],
        services=["mysql"],
        cluster_id="c-mysql",
        templates=[],
    )


def test_mysql_invalid_datadir_routes_to_restore_config():
    plan = ClaudeStylePlan(
        goal="restore mysql",
        context_analysis="mysql failed",
        proposed_approach="restart mysql",
        execution_steps=[
            ExecutionStep(
                step_id=1,
                title="Restart MySQL",
                phase="Execute",
                explanation="restart service",
                commands=[StepCommand(tool_name="node_agent", target="mysql", command="mysql.restart", operation="restart")],
            )
        ],
    )
    updated = _analyzer()._ensure_lab_node_agent_steps(plan, _mysql_bad_config_cluster())
    ops = _operations(updated)
    assert ("mysql", "restore_config") in ops
    assert ("mysql", "restart") not in ops


def test_mysql_bad_config_emits_root_mutate_restore_runner():
    """The generated FixingPlan step uses the restore wrapper argv as_root + mutate."""
    analyzer = _analyzer()
    plan = ClaudeStylePlan(
        goal="restore mysql",
        context_analysis="mysql config error",
        proposed_approach="restore config",
        execution_steps=[
            ExecutionStep(
                step_id=1, title="Restore MySQL", phase="Execute", explanation="restore",
                commands=[StepCommand(tool_name="node_agent", target="mysql", command="mysql.restore_config", operation="restore_config")],
            )
        ],
    )
    rca = RootCauseReport(
        report_id="rca_1", target_node_id="target-1", affected_service="mysql",
        root_cause="bad config", confidence=0.9,
    )
    fixing = analyzer._to_fixing_plan(plan, _mysql_bad_config_cluster(), "diag_mysql_1", rca)

    assert fixing.schema_version == "3.0"
    step = fixing.steps[0]
    assert step.runner.argv == ["/usr/local/sbin/aads-mysql-restore-config"]
    assert step.runner.as_root is True
    assert step.runner.side_effect == "mutate"
    # verification is a read-only runner with declarative extract
    assert step.verification.runner.side_effect == "read"
    assert step.verification.expected == {"alive": True}
    assert "alive" in step.verification.extract
    # rollback + snapshot are service-matched mutate runners
    assert fixing.rollback.enabled is True
    assert fixing.rollback.runner.argv == ["/usr/local/sbin/aads-mysql-restore-config"]
    assert fixing.pre_execution_snapshot.runner.argv == ["/usr/local/sbin/aads-mysql-ensure-config-snapshot"]
    assert fixing.final_verification.expected == {"alive": True}


def test_each_service_repair_emits_runner_specs():
    analyzer = _analyzer()
    cases = {
        "nginx": (["systemctl", "is-active", "nginx"], "/usr/local/sbin/aads-nginx-start", "start"),
        "postgresql": (["pg_isready"], "/usr/local/sbin/aads-postgresql-restart", "restart"),
        "redis": (["redis-cli", "ping"], "/usr/local/sbin/aads-redis-restart", "restart"),
    }
    for service, (_probe_argv, wrapper, operation) in cases.items():
        plan = ClaudeStylePlan(
            goal=f"fix {service}",
            context_analysis="down",
            proposed_approach="restart",
            execution_steps=[
                ExecutionStep(
                    step_id=1, title=f"Repair {service}", phase="Execute", explanation="repair",
                    commands=[StepCommand(tool_name="node_agent", target=service, command=f"{service}.{operation}", operation=operation)],
                )
            ],
        )
        rca = RootCauseReport(
            report_id="rca_x", target_node_id="target-1", affected_service=service,
            root_cause="down", confidence=0.8,
        )
        cluster = SimpleNamespace(anomalies=[], containers=[], services=[service], cluster_id="c", templates=[])
        fixing = analyzer._to_fixing_plan(plan, cluster, f"diag_{service}", rca)
        step = fixing.steps[0]
        assert step.runner.argv == [wrapper]
        assert step.runner.side_effect == "mutate"
        assert step.verification.runner.side_effect == "read"
        assert step.verification.extract  # non-empty declarative extractor
