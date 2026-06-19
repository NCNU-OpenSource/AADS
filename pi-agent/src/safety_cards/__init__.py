"""
Safety cards for the On-Device Agent hook pipeline (ADR-005).

A safety card is a hook that can return ``deny`` from ``before_run`` to block
execution. v1 shipped only the allow-all AuditHook; the PolicyCard here closes
the trade-off recorded in docs/runner-v2-plan.md §6 by enforcing the plan's
ExecutionProfile permission manifest on-device.
"""
from safety_cards.policy_card import (
    CommandPermission,
    ExecutionProfile,
    HookDecision,
    PathPermission,
    PolicyCard,
    profile_allows,
)

__all__ = [
    "CommandPermission",
    "ExecutionProfile",
    "HookDecision",
    "PathPermission",
    "PolicyCard",
    "profile_allows",
]
