"""
Action Plan Schema for Layer 2 Agent Output

Defines structured output format for Agent decisions to enable:
1. Layer 3 Dashboard rendering (Human-in-the-loop approval UI)
2. Layer 4 Executor Agent (constrained execution with guardrails)
3. Agentic Handoff protocol in Multi-Agent System

Design Philosophy (ADR-004):
- Structured JSON prevents Layer 4 Agent Drift & Hallucination
- JSON is the "constraint" that tells Layer 4: "Execute only what's defined, no inventing new plans"
- Layer 2 thinks, Layer 3 approves, Layer 4 executes

Related: ADR-002 Structured Output Decision, ADR-004 Claude Style Plan Design
"""
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


# ============================================================
# NEW: Claude Code Style Plan Schema
# ============================================================

class StepCommand(BaseModel):
    """
    Low-level command within an execution step

    Represents the actual command that Layer 4 Executor Agent will run.
    """
    tool_name: str = Field(
        ...,
        description="Tool to use: query_loki, query_prometheus, k8s_exec, bash"
    )
    target: str = Field(
        ...,
        description="Target system/service: Loki, nginx-pod, prometheus"
    )
    command: str = Field(
        ...,
        description="Actual command or query to execute"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "tool_name": "query_loki",
                    "target": "Loki",
                    "command": "{container=\"nginx\"} |= \"error\" | limit 100"
                },
                {
                    "tool_name": "k8s_exec",
                    "target": "nginx-deployment",
                    "command": "kubectl rollout restart deployment/nginx"
                }
            ]
        }


class ExecutionStep(BaseModel):
    """
    Single execution step in Claude Code TODO List style

    Designed for:
    - Layer 3 Dashboard: Render as TODO item with Approve checkbox
    - Layer 4 Executor: Strict execution boundary (no improvisation allowed)

    The `requires_approval` flag triggers HITL gate in Layer 3.
    """
    step_id: int = Field(..., ge=1, description="Sequential step ID")
    title: str = Field(
        ...,
        description="Short action title (e.g., '重啟 nginx 容器')"
    )
    phase: Literal["Explore", "Execute", "Verify"] = Field(
        ...,
        description="Step phase: Explore (read-only), Execute (modify), Verify (validate)"
    )
    explanation: str = Field(
        ...,
        description="Why this step is necessary (shown in Dashboard details)"
    )
    requires_approval: bool = Field(
        default=False,
        description="True for destructive Execute steps - triggers HITL approval in Layer 3"
    )
    status: Literal["pending", "approved", "running", "success", "failed", "skipped"] = Field(
        default="pending",
        description="Execution status tracked by Layer 4"
    )
    commands: List[StepCommand] = Field(
        ...,
        description="Commands for Layer 4 to execute (strict boundary)"
    )

    # Backward compatibility fields (mapped from commands[0] if needed)
    action_type: Literal["k8s_exec", "query", "verify"] = Field(
        default="query",
        description="[Deprecated] Use phase instead"
    )
    target: str = Field(
        default="",
        description="[Deprecated] Use commands[].target instead"
    )
    command: str = Field(
        default="",
        description="[Deprecated] Use commands[].command instead"
    )
    is_destructive: bool = Field(
        default=False,
        description="[Deprecated] Use requires_approval instead"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "step_id": 1,
                    "title": "檢查 nginx 錯誤日誌",
                    "phase": "Explore",
                    "explanation": "確認錯誤模式和頻率，判斷是否為持續性問題",
                    "requires_approval": False,
                    "status": "pending",
                    "commands": [
                        {
                            "tool_name": "query_loki",
                            "target": "Loki",
                            "command": "{container=\"nginx\"} |= \"error\" | limit 100"
                        }
                    ]
                },
                {
                    "step_id": 2,
                    "title": "重啟 nginx 服務",
                    "phase": "Execute",
                    "explanation": "清除連接池並重新載入配置",
                    "requires_approval": True,
                    "status": "pending",
                    "commands": [
                        {
                            "tool_name": "k8s_exec",
                            "target": "nginx-deployment",
                            "command": "kubectl rollout restart deployment/nginx"
                        }
                    ]
                }
            ]
        }


class ClaudeStylePlan(BaseModel):
    """
    Claude Code Plan Mode style remediation plan

    This is the PRIMARY output format for Layer 2 Agent.
    Designed for Multi-Agent handoff:
    - Layer 3: Renders goal/context as header, steps as TODO list
    - Layer 4: Receives this JSON as execution contract (no deviation allowed)

    Key principle: Layer 4 Executor Agent is CONSTRAINED to execute only
    what's defined in execution_steps. It cannot invent new plans.
    """
    goal: str = Field(
        ...,
        description="One sentence summarizing the fix objective"
    )
    context_analysis: str = Field(
        ...,
        description="Analysis of current system state and root cause"
    )
    proposed_approach: str = Field(
        ...,
        description="High-level remediation strategy"
    )
    execution_steps: List[ExecutionStep] = Field(
        ...,
        description="Ordered TODO list - Layer 4's execution contract"
    )

    # Backward compatibility with ActionPlan
    root_cause: str = Field(
        default="",
        description="[Deprecated] Use context_analysis instead"
    )
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Agent's confidence in the diagnosis (0.0-1.0)"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "goal": "恢復 nginx 服務正常響應，解決連接池耗盡問題",
                    "context_analysis": "nginx 容器出現大量 502 錯誤，連接池達到上限，後端服務響應緩慢導致連接堆積",
                    "proposed_approach": "先確認問題範圍，重啟服務清除連接池，最後驗證恢復狀態",
                    "execution_steps": [
                        {
                            "step_id": 1,
                            "title": "檢查連接池狀態",
                            "phase": "Explore",
                            "explanation": "確認當前連接數是否超過閾值",
                            "requires_approval": False,
                            "commands": [{"tool_name": "bash", "target": "nginx-pod", "command": "netstat -an | grep ESTABLISHED | wc -l"}]
                        },
                        {
                            "step_id": 2,
                            "title": "重啟 nginx 服務",
                            "phase": "Execute",
                            "explanation": "清除連接池並重新載入配置",
                            "requires_approval": True,
                            "commands": [{"tool_name": "k8s_exec", "target": "nginx-deployment", "command": "kubectl rollout restart deployment/nginx"}]
                        },
                        {
                            "step_id": 3,
                            "title": "驗證服務恢復",
                            "phase": "Verify",
                            "explanation": "確認 nginx 正常響應且無新錯誤",
                            "requires_approval": False,
                            "commands": [{"tool_name": "bash", "target": "nginx-pod", "command": "curl -s -o /dev/null -w '%{http_code}' http://localhost:80/health"}]
                        }
                    ],
                    "confidence_score": 0.85
                }
            ]
        }


# ============================================================
# LEGACY: Original ActionStep/ActionPlan (kept for backward compatibility)
# ============================================================

class ActionStep(BaseModel):
    """
    [DEPRECATED] Use ExecutionStep instead.

    Kept for backward compatibility with existing diagnosis reports.
    """
    step_id: int = Field(..., description="Sequential step ID", ge=1)
    description: str = Field(..., description="Human-readable step description")
    action_type: Literal["k8s_exec", "query", "verify"] = Field(
        ...,
        description="Type of action: k8s_exec (Kubernetes command), query (read-only query), verify (validation check)"
    )
    target: str = Field(..., description="Target container/service/endpoint")
    command: str = Field(..., description="Command to execute")
    is_destructive: bool = Field(
        default=False,
        description="True if action modifies state (requires human approval)"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "step_id": 1,
                    "description": "Check nginx error logs",
                    "action_type": "query",
                    "target": "nginx-pod",
                    "command": "kubectl logs nginx-pod --tail=100",
                    "is_destructive": False
                }
            ]
        }


class ActionPlan(BaseModel):
    """
    [DEPRECATED] Use ClaudeStylePlan instead.

    Kept for backward compatibility with existing diagnosis reports.
    """
    root_cause: str = Field(..., description="Root cause analysis summary")
    confidence_score: float = Field(
        ...,
        description="Confidence level in diagnosis",
        ge=0.0,
        le=1.0
    )
    actions: List[ActionStep] = Field(
        default_factory=list,
        description="Ordered list of action steps"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "root_cause": "Connection pool exhaustion in nginx due to backend timeout",
                    "confidence_score": 0.85,
                    "actions": [
                        {
                            "step_id": 1,
                            "description": "Verify connection pool status",
                            "action_type": "query",
                            "target": "nginx-1",
                            "command": "netstat -an | grep ESTABLISHED | wc -l",
                            "is_destructive": False
                        }
                    ]
                }
            ]
        }

