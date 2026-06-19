"""
Layer 2 Output Schemas

Primary executable: FixingPlan (Three-Agent strict sequence)
Legacy display: ClaudeStylePlan (Claude Code Plan Mode style)
Legacy: ActionPlan, ActionStep (backward compatibility)
"""
from schemas.action_plan import (
    # Executable v2 schemas
    RootCauseEvidence,
    RootCauseReport,
    VerificationSpec,
    PreExecutionSnapshot,
    FixingPlanStep,
    PlanSelfCheck,
    FixingPlan,
    # Execution profile (ADR-005)
    CommandPermission,
    PathPermission,
    ExecutionProfile,
    profile_allows,
    # Legacy Claude Style Plan
    StepCommand,
    ExecutionStep,
    ClaudeStylePlan,
    # Legacy (deprecated but kept for backward compatibility)
    ActionStep,
    ActionPlan,
)

__all__ = [
    "RootCauseEvidence",
    "RootCauseReport",
    "VerificationSpec",
    "PreExecutionSnapshot",
    "FixingPlanStep",
    "PlanSelfCheck",
    "FixingPlan",
    "CommandPermission",
    "PathPermission",
    "ExecutionProfile",
    "profile_allows",
    "StepCommand",
    "ExecutionStep",
    "ClaudeStylePlan",
    "ActionStep",
    "ActionPlan",
]
