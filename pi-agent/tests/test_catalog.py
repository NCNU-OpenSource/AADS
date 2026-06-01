import importlib.util
import subprocess
from pathlib import Path

SRC_MAIN = Path(__file__).resolve().parents[1] / "src" / "main.py"
spec = importlib.util.spec_from_file_location("pi_agent_main", SRC_MAIN)
pi_agent_main = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(pi_agent_main)

CATALOG = pi_agent_main.CATALOG


def test_nginx_start_is_idempotent_action():
    item = CATALOG["nginx.start"]
    assert item["scope"] == "action"
    assert item["idempotent"] is True
    assert item["sudo_wrapper"] == "/usr/local/sbin/aads-nginx-start"


def test_nginx_reload_is_not_retried_in_v1():
    item = CATALOG["nginx.reload"]
    assert item["scope"] == "action"
    assert item["idempotent"] is False
    assert item["retry_policy"]["max_attempts"] == 1


def test_journal_tail_has_unit_allowlist():
    item = CATALOG["system.journal_tail"]
    assert item["scope"] == "probe"
    assert "unit" in item["arg_allowlist"]


def test_snapshot_command_is_idempotent_action():
    item = CATALOG["nginx.ensure_known_good_snapshot"]
    assert item["scope"] == "action"
    assert item["idempotent"] is True
    assert item["sudo_wrapper"] == "/usr/local/sbin/aads-nginx-ensure-known-good-snapshot"


def test_http_check_is_structured_probe():
    item = CATALOG["nginx.http_check"]
    assert item["scope"] == "probe"
    assert item["idempotent"] is True
    assert "url_prefixes" in item["arg_allowlist"]


def test_sudoers_health_uses_effective_grants_when_file_is_unreadable(monkeypatch):
    class UnreadablePath:
        def exists(self):
            raise PermissionError("permission denied")

    def fake_run(argv, capture_output, text, timeout, check):
        assert argv == ["sudo", "-n", "-l", "/usr/local/sbin/aads-nginx-start"]
        return subprocess.CompletedProcess(argv, 0, stdout="/usr/local/sbin/aads-nginx-start\n", stderr="")

    monkeypatch.setattr(pi_agent_main, "Path", lambda _: UnreadablePath())
    monkeypatch.setattr(pi_agent_main, "WRAPPERS", {"nginx.start": "/usr/local/sbin/aads-nginx-start"})
    monkeypatch.setattr(pi_agent_main.subprocess, "run", fake_run)

    assert pi_agent_main.sudoers_health_problems() == []
