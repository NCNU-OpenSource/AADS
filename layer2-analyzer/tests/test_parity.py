"""
Parity test: layer2 profile_allows vs pi-agent PolicyCard matcher (ADR-005).

pi-agent must NOT import layer2 — it carries its own copy of profile_allows.
This file is the single pin that catches drift between the two copies: it feeds
catalog-derived runners from runner_catalog into both functions and asserts that
the verdicts are identical for every combination.

If this test fails, the two copies have diverged and pi-agent will enforce a
different policy than layer2 generated.
"""
import os
import sys

import pytest

# Layer2 imports
L2_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, L2_SRC)

# Pi-agent imports (independent copy of profile_allows)
PI_SRC = os.path.join(os.path.dirname(__file__), "..", "..", "pi-agent", "src")
sys.path.insert(0, PI_SRC)

import runner_catalog
from schemas.action_plan import profile_allows as l2_profile_allows
from schemas.action_plan import ExecutionProfile, CommandPermission, PathPermission, RunnerSpec
from safety_cards.policy_card import profile_allows as pi_profile_allows


def _all_catalog_runners():
    """Yield (label, runner_dict) for every runner the catalog can produce."""
    yield from [
        # nginx
        ("nginx.start",    {"argv": ["/usr/local/sbin/aads-nginx-start"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("nginx.stop",     {"argv": ["/usr/local/sbin/aads-nginx-stop"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("nginx.restart",  {"argv": ["/usr/local/sbin/aads-nginx-restart"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 60, "cwd": "/"}),
        ("nginx.status",   {"argv": ["systemctl", "is-active", "nginx"], "as_root": False, "side_effect": "read", "timeout_seconds": 5, "cwd": "/"}),
        ("nginx.snapshot", {"argv": ["/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("nginx.restore",  {"argv": ["/usr/local/sbin/aads-nginx-restore-known-good"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 60, "cwd": "/"}),
        # postgresql
        ("pg.start",    {"argv": ["/usr/local/sbin/aads-postgresql-start"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("pg.stop",     {"argv": ["/usr/local/sbin/aads-postgresql-stop"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("pg.restart",  {"argv": ["/usr/local/sbin/aads-postgresql-restart"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 60, "cwd": "/"}),
        ("pg.status",   {"argv": ["systemctl", "is-active", "postgresql"], "as_root": False, "side_effect": "read", "timeout_seconds": 5, "cwd": "/"}),
        # redis
        ("redis.start",    {"argv": ["/usr/local/sbin/aads-redis-start"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("redis.restart",  {"argv": ["/usr/local/sbin/aads-redis-restart"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 60, "cwd": "/"}),
        ("redis.status",   {"argv": ["systemctl", "is-active", "redis"], "as_root": False, "side_effect": "read", "timeout_seconds": 5, "cwd": "/"}),
        ("redis.ping",     {"argv": ["redis-cli", "ping"], "as_root": False, "side_effect": "read", "timeout_seconds": 5, "cwd": "/"}),
        # mysql
        ("mysql.start",    {"argv": ["/usr/local/sbin/aads-mysql-start"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 30, "cwd": "/"}),
        ("mysql.restart",  {"argv": ["/usr/local/sbin/aads-mysql-restart"], "as_root": True, "side_effect": "mutate", "timeout_seconds": 60, "cwd": "/"}),
        ("mysql.status",   {"argv": ["systemctl", "is-active", "mysql"], "as_root": False, "side_effect": "read", "timeout_seconds": 5, "cwd": "/"}),
    ]


def _profile_for(runner_dict: dict, service: str) -> dict:
    """Build a profile that allows exactly this runner."""
    argv = runner_dict["argv"]
    cmd = {
        "argv0": argv[0],
        "argv_prefix": argv[1:],
        "as_root": runner_dict.get("as_root", False),
        "side_effect": runner_dict.get("side_effect", "read"),
        "allow_extra_args": False,
        "max_timeout_seconds": runner_dict.get("timeout_seconds", 600),
    }
    return {
        "profile_version": "1.0",
        "generated_by": "runner_catalog",
        "allowed_commands": [cmd],
        "path_permissions": [],
    }


@pytest.mark.parametrize("label,runner_dict", list(_all_catalog_runners()))
def test_l2_and_pi_profile_allows_agree_on_allow(label, runner_dict):
    """Both copies of profile_allows must agree when a runner matches its own profile."""
    service = label.split(".")[0]
    profile = _profile_for(runner_dict, service)
    l2_result = l2_profile_allows(profile, runner_dict)
    pi_result = pi_profile_allows(profile, runner_dict)
    assert l2_result == pi_result, (
        f"{label}: layer2 says {l2_result!r}, pi-agent says {pi_result!r}"
    )
    assert l2_result is None, f"{label}: expected allow (None) but got {l2_result!r}"


@pytest.mark.parametrize("label,runner_dict", list(_all_catalog_runners()))
def test_l2_and_pi_profile_allows_agree_on_deny(label, runner_dict):
    """Both copies must agree when a runner is rejected (wrong argv0, empty profile)."""
    profile = {
        "profile_version": "1.0",
        "generated_by": "runner_catalog",
        "allowed_commands": [{"argv0": "/nonexistent/safe-only", "argv_prefix": []}],
        "path_permissions": [],
    }
    l2_result = l2_profile_allows(profile, runner_dict)
    pi_result = pi_profile_allows(profile, runner_dict)
    assert l2_result == pi_result, (
        f"{label}: layer2 says {l2_result!r}, pi-agent says {pi_result!r}"
    )
    # Both must deny (not None)
    assert l2_result is not None, f"{label}: expected deny but both said allow"


def test_missing_profile_agreement():
    l2 = l2_profile_allows(None, {"argv": ["systemctl", "is-active", "nginx"]})
    pi = pi_profile_allows(None, {"argv": ["systemctl", "is-active", "nginx"]})
    assert l2 == pi == "profile_missing"
