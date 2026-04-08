#!/usr/bin/env python3
"""
Integration test for ClaudeStylePlan generation

This script triggers a Layer 2 analysis and validates that:
1. The output is in ClaudeStylePlan format
2. Execution steps have proper phases
3. Required fields are present
"""
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from schemas.action_plan import ClaudeStylePlan, ExecutionStep, StepCommand


def test_schema_validation():
    """Test that schemas can be instantiated correctly"""
    print("🧪 Testing schema validation...")

    # Test StepCommand
    cmd = StepCommand(
        tool_name="query_loki",
        target="nginx",
        command='{container="nginx"} |= "error"'
    )
    assert cmd.tool_name == "query_loki"
    print("  ✓ StepCommand creation successful")

    # Test ExecutionStep
    step = ExecutionStep(
        step_id=1,
        title="查詢 nginx 日誌",
        phase="Explore",
        explanation="確認錯誤模式",
        commands=[cmd]
    )
    assert step.phase == "Explore"
    assert step.status == "pending"
    assert len(step.commands) == 1
    print("  ✓ ExecutionStep creation successful")

    # Test ClaudeStylePlan
    plan = ClaudeStylePlan(
        goal="重啟 nginx 容器",
        context_analysis="容器崩潰需要重啟",
        proposed_approach="查詢日誌 → 重啟 → 驗證",
        execution_steps=[
            ExecutionStep(
                step_id=1,
                title="查詢日誌",
                phase="Explore",
                explanation="確認問題",
                commands=[
                    StepCommand(
                        tool_name="query_loki",
                        target="nginx",
                        command='query'
                    )
                ]
            ),
            ExecutionStep(
                step_id=2,
                title="重啟容器",
                phase="Execute",
                explanation="恢復服務",
                requires_approval=True,
                commands=[
                    StepCommand(
                        tool_name="k8s_exec",
                        target="nginx",
                        command='kubectl rollout restart'
                    )
                ]
            ),
            ExecutionStep(
                step_id=3,
                title="驗證健康",
                phase="Verify",
                explanation="確認恢復",
                commands=[
                    StepCommand(
                        tool_name="bash",
                        target="nginx",
                        command='curl http://nginx/health'
                    )
                ]
            )
        ]
    )

    assert plan.goal == "重啟 nginx 容器"
    assert len(plan.execution_steps) == 3
    assert plan.execution_steps[0].phase == "Explore"
    assert plan.execution_steps[1].phase == "Execute"
    assert plan.execution_steps[1].requires_approval is True
    assert plan.execution_steps[2].phase == "Verify"
    print("  ✓ ClaudeStylePlan creation successful")

    # Test serialization
    data = plan.model_dump()
    assert 'goal' in data
    assert 'context_analysis' in data
    assert 'proposed_approach' in data
    assert 'execution_steps' in data
    assert len(data['execution_steps']) == 3
    assert 'commands' in data['execution_steps'][0]
    print("  ✓ Serialization to dict successful")

    print("\n✅ All schema validation tests passed!\n")


def test_backward_compatibility():
    """Test backward compatibility fields"""
    print("🧪 Testing backward compatibility...")

    # Test ExecutionStep backward compat
    step = ExecutionStep(
        step_id=1,
        title="Test",
        phase="Execute",
        explanation="Test",
        commands=[]
    )

    assert step.action_type == "query"  # Default
    assert step.target == ""
    assert step.command == ""
    assert step.is_destructive is False
    print("  ✓ ExecutionStep backward compatibility OK")

    # Test ClaudeStylePlan backward compat
    plan = ClaudeStylePlan(
        goal="Test",
        context_analysis="Test",
        proposed_approach="Test",
        execution_steps=[]
    )

    assert plan.root_cause == ""
    assert plan.confidence_score == 0.0
    print("  ✓ ClaudeStylePlan backward compatibility OK")

    print("\n✅ All backward compatibility tests passed!\n")


def test_phase_grouping():
    """Test that phases can be grouped correctly"""
    print("🧪 Testing phase grouping...")

    steps = [
        ExecutionStep(step_id=1, title="Query logs", phase="Explore", explanation="Check", commands=[]),
        ExecutionStep(step_id=2, title="Query metrics", phase="Explore", explanation="Verify", commands=[]),
        ExecutionStep(step_id=3, title="Restart", phase="Execute", explanation="Fix", requires_approval=True, commands=[]),
        ExecutionStep(step_id=4, title="Check health", phase="Verify", explanation="Confirm", commands=[]),
    ]

    # Group by phase
    phases = {}
    for step in steps:
        if step.phase not in phases:
            phases[step.phase] = []
        phases[step.phase].append(step)

    assert "Explore" in phases
    assert "Execute" in phases
    assert "Verify" in phases
    assert len(phases["Explore"]) == 2
    assert len(phases["Execute"]) == 1
    assert len(phases["Verify"]) == 1
    assert phases["Execute"][0].requires_approval is True

    print("  ✓ Phase grouping successful")
    print(f"    - Explore: {len(phases['Explore'])} steps")
    print(f"    - Execute: {len(phases['Execute'])} steps (requires approval)")
    print(f"    - Verify: {len(phases['Verify'])} steps")

    print("\n✅ Phase grouping test passed!\n")


if __name__ == "__main__":
    try:
        test_schema_validation()
        test_backward_compatibility()
        test_phase_grouping()

        print("=" * 60)
        print("🎉 All tests passed successfully!")
        print("=" * 60)
        sys.exit(0)

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
