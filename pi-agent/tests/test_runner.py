import importlib.util
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

SRC_MAIN = Path(__file__).resolve().parents[1] / "src" / "main.py"
spec = importlib.util.spec_from_file_location("pi_agent_main", SRC_MAIN)
pi_agent_main = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(pi_agent_main)

RunnerSpec = pi_agent_main.RunnerSpec
CommandRunner = pi_agent_main.CommandRunner

TEST_TOKEN = "test-token"


def client(monkeypatch):
    monkeypatch.setattr(pi_agent_main, "TOKEN", TEST_TOKEN)
    return TestClient(pi_agent_main.app)


def auth():
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def test_argv_runner_does_not_enable_shell_expansion():
    # shell=False means $HOME is passed literally, never expanded by a shell.
    result = CommandRunner().run(RunnerSpec(argv=["echo", "$HOME"]))
    assert result["status"] == "success"
    assert result["stdout"].strip() == "$HOME"


def test_as_root_false_does_not_use_sudo(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, stdout="active\n", stderr="")

    monkeypatch.setattr(pi_agent_main.subprocess, "run", fake_run)
    CommandRunner().run(RunnerSpec(argv=["systemctl", "is-active", "nginx"], as_root=False))
    assert captured["argv"] == ["systemctl", "is-active", "nginx"]
    assert "sudo" not in captured["argv"]


def test_as_root_true_routes_through_single_root_runner(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(pi_agent_main.subprocess, "run", fake_run)
    CommandRunner().run(
        RunnerSpec(argv=["/usr/local/sbin/aads-mysql-restart"], as_root=True, side_effect="mutate")
    )
    assert captured["argv"] == ["sudo", "-n", pi_agent_main.ROOT_RUNNER]
    # argv is handed to the root runner as JSON over stdin, never as a shell string.
    payload = json.loads(captured["input"])
    assert payload["argv"] == ["/usr/local/sbin/aads-mysql-restart"]


def test_hook_deny_skips_execution(monkeypatch):
    class DenyHook:
        card_id = "test.deny.v1"

        def before_run(self, request, sha):
            return pi_agent_main.HookDecision(decision="deny", card_id=self.card_id, reason="blocked by test")

        def after_run(self, request, result):
            return pi_agent_main.HookDecision(decision="allow", card_id=self.card_id)

    ran = {"called": False}

    def fake_run(*args, **kwargs):
        ran["called"] = True
        return subprocess.CompletedProcess(["x"], 0, stdout="", stderr="")

    monkeypatch.setattr(pi_agent_main, "HOOKS", [DenyHook()])
    monkeypatch.setattr(pi_agent_main.subprocess, "run", fake_run)
    resp = client(monkeypatch).post(
        "/v1/commands/run",
        headers=auth(),
        json={"runner": {"argv": ["rm", "-rf", "/"]}, "context": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "blocked"
    assert body["hook"]["decision"] == "deny"
    assert ran["called"] is False


def test_audit_hook_response_contains_decision_context_and_argv_hash(monkeypatch):
    resp = client(monkeypatch).post(
        "/v1/commands/run",
        headers=auth(),
        json={
            "runner": {"argv": ["echo", "hello"]},
            "context": {"service": "nginx", "operation": "status", "plan_id": "p1", "step_id": 2},
            "execution_profile": {
                "allowed_commands": [{"argv0": "echo", "argv_prefix": ["hello"]}],
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "success"
    assert body["stdout"].strip() == "hello"
    assert body["hook"]["decision"] == "allow"
    assert body["hook"]["card_id"] == "audit.allow_all.v1"
    assert len(body["hook"]["argv_sha256"]) == 64
    assert body["hook"]["side_effect"] == "read"
    assert body["context"]["service"] == "nginx"
    assert body["context"]["step_id"] == 2


def test_facts_returns_runner_capabilities_not_catalog(monkeypatch):
    resp = client(monkeypatch).get("/v1/node/facts", headers=auth())
    assert resp.status_code == 200
    body = resp.json()
    assert "supported_commands" not in body
    caps = body["runner_capabilities"]
    assert caps["modes"] == ["argv"]
    assert caps["supports_as_root"] is True
    assert caps["hook_default"] == "policy_card+audit"
    assert caps["policy_mode"] == "enforce"


def test_unauthorized_without_token(monkeypatch):
    resp = client(monkeypatch).get("/v1/node/facts")
    assert resp.status_code == 401
