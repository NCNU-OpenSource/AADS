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
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================
# EXECUTABLE: Three-Agent FixingPlan v2 schema
# ============================================================


class RootCauseEvidence(BaseModel):
    """Structured evidence emitted by the On-Device Agent RCA path."""

    source: Literal["log", "metric", "probe", "trace", "audit"] = Field(
        ...,
        description="Evidence source type"
    )
    summary: str = Field(..., min_length=1, description="Short evidence summary")
    references: List[str] = Field(default_factory=list, description="Log ids, metric names, or probe ids")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RootCauseReport(BaseModel):
    """On-Device Agent RCA output contract."""

    schema_version: str = Field(default="1.0")
    report_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    affected_service: str = Field(..., min_length=1)
    root_cause: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: List[RootCauseEvidence] = Field(default_factory=list)
    recommended_capabilities: List[str] = Field(default_factory=list)


class RunnerSpec(BaseModel):
    """
    argv-first command runner spec executed on the target by the On-Device Agent.

    Replaces the legacy catalog ``command_id`` contract. The safety boundary is no
    longer "is this command_id in the catalog" but the full per-execution spec +
    context + hook decision + audit. argv is run with shell=False; there is no
    implicit shell expansion, pipe, or redirect. ``as_root=true`` routes through a
    single root runner wrapper. ``side_effect`` (not ``as_root``) tells Layer 4
    whether the command mutates state and therefore needs a node lock / snapshot.
    """

    mode: Literal["argv"] = Field(default="argv")
    argv: List[str] = Field(..., min_length=1)
    cwd: str = Field(default="/")
    env: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    as_root: bool = Field(default=False)
    side_effect: Literal["read", "mutate"] = Field(default="read")
    stdin: Optional[str] = Field(default=None)

    @field_validator("argv")
    @classmethod
    def argv_must_be_non_empty_strings(cls, value: List[str]) -> List[str]:
        if not value or any((not isinstance(arg, str) or arg == "") for arg in value):
            raise ValueError("runner.argv must be a non-empty list of non-empty strings")
        return value


class ExtractRule(BaseModel):
    """
    Declarative extractor that turns one slice of runner output into a single
    structured ``observed`` field, so verification compares structured values
    (never free text). ``from`` is the trust boundary: only these five sources.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_: Literal["stdout", "stderr", "stdout_stripped", "stderr_stripped", "returncode"] = Field(
        ..., alias="from"
    )
    equals: Optional[str] = Field(default=None, description="-> bool: source == equals")
    contains: Optional[str] = Field(default=None, description="-> bool: contains in source")
    regex: Optional[str] = Field(default=None, description="-> bool: re.search matched")
    as_int: bool = Field(default=False, description="-> int(stripped source)")
    json_path: Optional[str] = Field(default=None, description="-> value at dotted path in JSON source")


class VerificationSpec(BaseModel):
    """
    Deterministic verification contract (runner-based).

    Verification runs a read-only runner, extracts structured fields via
    ``extract``, and compares them against ``expected``. Free text / LLM
    judgement is never allowed.
    """

    type: Literal["runner_probe"] = Field(default="runner_probe")
    runner: RunnerSpec
    extract: Dict[str, ExtractRule] = Field(default_factory=dict)
    expected: Dict[str, Any] = Field(..., description="Structured expected fields")

    @field_validator("expected")
    @classmethod
    def expected_must_be_structured(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if not value:
            raise ValueError("verification.expected must not be empty")
        if "text" in value or "prompt" in value or "llm_judge" in value:
            raise ValueError("free-text or LLM verification is not allowed")
        return value

    @model_validator(mode="after")
    def runner_must_be_read_and_extract_covers_expected(self):
        if self.runner.side_effect != "read":
            raise ValueError("verification.runner.side_effect must be 'read'")
        missing = set(self.expected.keys()) - set(self.extract.keys())
        if missing:
            raise ValueError(f"verification.extract must cover expected keys: {sorted(missing)}")
        return self


class PreExecutionSnapshot(BaseModel):
    enabled: bool = Field(default=False)
    runner: Optional[RunnerSpec] = Field(default=None)
    scope: Literal["nginx_config", "postgresql_config", "redis_config", "mysql_config"] = Field(default="nginx_config")
    on_failure: Literal["block", "continue_if_existing"] = Field(default="block")

    @model_validator(mode="after")
    def runner_required_when_enabled(self):
        if self.enabled and self.runner is None:
            raise ValueError("pre_execution_snapshot.runner is required when enabled")
        if self.runner is not None and self.runner.side_effect != "mutate":
            raise ValueError("snapshot runner must be side_effect=mutate")
        return self


class RollbackSpec(BaseModel):
    enabled: bool = Field(default=False)
    runner: Optional[RunnerSpec] = Field(default=None)

    @model_validator(mode="after")
    def runner_required_when_enabled(self):
        if self.enabled and self.runner is None:
            raise ValueError("rollback.runner is required when enabled")
        if self.runner is not None and self.runner.side_effect != "mutate":
            raise ValueError("rollback runner must be side_effect=mutate")
        return self


class FixingPlanStep(BaseModel):
    """Single strictly ordered executable step (runner-based)."""

    step_id: int = Field(..., ge=1)
    order: int = Field(..., ge=1)
    runner: RunnerSpec
    context: Dict[str, Any] = Field(default_factory=dict)
    idempotency: Dict[str, Any] = Field(
        default_factory=lambda: {"mode": "idempotent", "max_attempts": 2}
    )
    expected_outcome: str = Field(..., min_length=1)
    on_failure: Literal["abort", "rollback"] = Field(default="abort")
    verification: VerificationSpec


class PlanSelfCheck(BaseModel):
    passed: bool = Field(..., description="System Agent self-check decision")
    rationale: str = Field(..., min_length=1)
    checked_items: List[str] = Field(default_factory=list)


class FixingPlan(BaseModel):
    """
    Executable System Agent plan for the controller-side Knowledge Agent.

    schema 3.0 replaces the legacy catalog ``command_id`` contract with runner
    specs. The Knowledge Agent must execute only this schema and must not infer
    additional actions.
    """

    schema_version: Literal["3.0"] = Field(default="3.0")
    plan_id: str = Field(..., min_length=1)
    rca_report_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    risk_level: Literal["low", "medium", "high", "critical"] = Field(default="low")
    environment_policy: Dict[str, Any] = Field(default_factory=dict)
    pre_execution_snapshot: PreExecutionSnapshot = Field(default_factory=PreExecutionSnapshot)
    rollback: RollbackSpec = Field(default_factory=RollbackSpec)
    steps: List[FixingPlanStep] = Field(..., min_length=1)
    final_verification: VerificationSpec
    self_check: PlanSelfCheck

    @model_validator(mode="after")
    def validate_order_and_policy(self):
        orders = [step.order for step in self.steps]
        if len(set(orders)) != len(orders):
            raise ValueError("FixingPlan.steps order values must be unique")
        if orders != sorted(orders):
            raise ValueError("FixingPlan.steps must be sorted by order")
        if self.environment_policy.get("environment") == "prod" and self.environment_policy.get("auto_execute_allowed") is True:
            raise ValueError("prod plans cannot be auto-executable")
        if not self.self_check.passed:
            raise ValueError("FixingPlan self_check must pass before storage")
        return self


# ============================================================
# LEGACY: Claude Code Style Plan Schema
# ============================================================

class StepCommand(BaseModel):
    """
    Low-level command within an execution step

    Represents the actual command that Layer 4 Executor Agent will run.

    Available tools:
    - query_loki: Query Loki logs using LogQL
    - query_prometheus: Query Prometheus metrics using PromQL
    - execute_diagnostic_command: Run whitelisted read-only shell commands
    """
    schema_version: str = Field(
        default="1.0",
        description="Command schema version understood by Layer 4"
    )
    tool_name: str = Field(
        ...,
        description="Tool to use: query_loki, query_prometheus, execute_diagnostic_command, node_agent"
    )
    operation: Optional[str] = Field(
        default=None,
        description="Runner operation for node_agent commands (e.g. restart, restore_config, status). "
                    "Combined with `target` (service) it maps to a RunnerSpec. Replaces legacy command_id."
    )
    target_node_id: Optional[str] = Field(
        default=None,
        description="Registered target node id for node_agent commands"
    )
    target: str = Field(
        ...,
        description="Target system/service/container"
    )
    command: str = Field(
        ...,
        description="Actual LogQL query, PromQL query, or shell command"
    )
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Typed arguments for catalog commands"
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description="Risk level used by Layer 4 policy checks"
    )
    environment_policy: Dict[str, Any] = Field(
        default_factory=dict,
        description="Inline target environment and auto-execution policy"
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
    schema_version: str = Field(
        default="1.0",
        description="Plan schema version; Layer 4 rejects unsupported versions"
    )
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
