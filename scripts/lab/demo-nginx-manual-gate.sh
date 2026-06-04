#!/usr/bin/env bash
# Prepare a one-click Nginx bad-config manual Gate demo.
#
# This script intentionally does NOT approve or execute a plan. It prepares a
# clean manual Dashboard demo by restoring the target baseline, optionally
# rejecting stale pending Gate items, injecting a broken nginx.conf, and waiting
# until the Dashboard queue shows a schema 3.0 restore plan.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONTROLLER_URL="${AADS_DASHBOARD_URL:-http://${AADS_CONTROLLER_IP:-100.72.172.83}:5000}"
TARGET_URL="${AADS_TARGET_AGENT_URL:-http://${AADS_TARGET_IP:-100.77.197.118}:8090}"
WAIT_SECONDS="${AADS_DEMO_WAIT_SECONDS:-360}"
RESET_QUEUE=true
VERIFY_PLAN=""

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--no-reset-queue] [--wait-seconds SECONDS]
  $(basename "$0") --verify [PLAN_ID]

Environment:
  AADS_CONTROLLER_IP       Controller IP (default: 100.72.172.83)
  AADS_TARGET_IP           Target IP (default: 100.77.197.118)
  AADS_DASHBOARD_URL       Full Dashboard URL override
  AADS_TARGET_AGENT_URL    Full target agent URL override
  AADS_DEMO_WAIT_SECONDS   Queue wait timeout (default: 360)

The default mode prepares the demo and stops before approve/execute.
Use --verify after the operator has executed the plan from the Dashboard.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-reset-queue)
      RESET_QUEUE=false
      shift
      ;;
    --wait-seconds)
      WAIT_SECONDS="${2:?--wait-seconds requires a value}"
      shift 2
      ;;
    --verify)
      VERIFY_PLAN="${2:-latest}"
      shift
      if [[ $# -gt 0 && "$1" != --* ]]; then
        VERIFY_PLAN="$1"
        shift
      fi
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

TOKEN_FILE="$ROOT/.aads-lab-token"
ADMIN_KEY_FILE="$ROOT/.aads-lab-admin-key"

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "ERROR: missing $TOKEN_FILE" >&2
  exit 2
fi
if [[ ! -f "$ADMIN_KEY_FILE" ]]; then
  echo "ERROR: missing $ADMIN_KEY_FILE" >&2
  exit 2
fi

export AADS_DEMO_ROOT="$ROOT"
export AADS_DEMO_CONTROLLER_URL="$CONTROLLER_URL"
export AADS_DEMO_TARGET_URL="$TARGET_URL"
export AADS_DEMO_WAIT_SECONDS="$WAIT_SECONDS"
export AADS_DEMO_RESET_QUEUE="$RESET_QUEUE"
export AADS_DEMO_VERIFY_PLAN="$VERIFY_PLAN"

python3 - <<'PY'
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(os.environ["AADS_DEMO_ROOT"])
dashboard_url = os.environ["AADS_DEMO_CONTROLLER_URL"].rstrip("/")
target_url = os.environ["AADS_DEMO_TARGET_URL"].rstrip("/")
wait_seconds = int(os.environ["AADS_DEMO_WAIT_SECONDS"])
reset_queue = os.environ["AADS_DEMO_RESET_QUEUE"].lower() == "true"
verify_plan = os.environ["AADS_DEMO_VERIFY_PLAN"]
expected_node_id = "3bf76430-cf5d-44c6-8ff2-ee0161f73740"
expected_loki_query = '{node_id="3bf76430-cf5d-44c6-8ff2-ee0161f73740"}'

token = (root / ".aads-lab-token").read_text().strip()
admin_key = (root / ".aads-lab-admin-key").read_text().strip()


def log(msg):
    print(f"[demo] {msg}", flush=True)


def parse_env_file(path):
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def validate_lab_env():
    env_path = root / ".env.lab"
    if not env_path.exists():
        raise RuntimeError("missing .env.lab; run from the AADS lab checkout before the demo")

    values = parse_env_file(env_path)
    expected = {
        "AADS_FORCE_GATE_APPROVAL": "true",
        "AADS_NODE_ID": expected_node_id,
        "AADS_DEFAULT_NODE_ID": expected_node_id,
        "LAYER1_LOKI_QUERY": expected_loki_query,
    }
    mismatches = []
    for key, expected_value in expected.items():
        actual = values.get(key)
        if actual != expected_value:
            mismatches.append(f"{key} expected {expected_value!r}, got {actual!r}")
    if mismatches:
        raise RuntimeError("lab env preflight failed: " + "; ".join(mismatches))
    log("lab env policy ok: manual Gate approval enabled and Loki query targets the demo node")


def request_json(method, url, *, body=None, headers=None, timeout=30, allow_error=False):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        if allow_error:
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"raw": raw}
            payload["http_status"] = exc.code
            return payload
        raise


def dashboard_json(method, path, *, body=None, timeout=30, allow_error=False):
    return request_json(
        method,
        f"{dashboard_url}{path}",
        body=body,
        timeout=timeout,
        allow_error=allow_error,
        headers={"X-Admin-API-Key": admin_key},
    )


def target_command(argv, *, as_root=False, side_effect="read", operation="manual", purpose="manual-demo", timeout=30, ok_rc=None):
    payload = {
        "schema_version": "runner.v1",
        "runner": {
            "argv": argv,
            "side_effect": side_effect,
            "as_root": as_root,
            "timeout_seconds": timeout,
        },
        "context": {
            "service": "nginx",
            "operation": operation,
            "purpose": purpose,
        },
    }
    result = request_json(
        "POST",
        f"{target_url}/v1/commands/run",
        body=payload,
        timeout=timeout + 5,
        headers={"Authorization": f"Bearer {token}"},
    )
    rc = result.get("returncode")
    status = result.get("status")
    stderr = (result.get("stderr") or "").strip()
    stdout = (result.get("stdout") or "").strip()
    log(f"runner {' '.join(argv[:3])}{' ...' if len(argv) > 3 else ''}: status={status} rc={rc}")
    if stdout:
        log(f"  stdout: {stdout[:240]}")
    if stderr:
        log(f"  stderr: {stderr[:240]}")
    if ok_rc is None:
        ok_rc = {0}
    if rc not in ok_rc:
        raise RuntimeError(f"unexpected returncode for {' '.join(argv)}: {rc}")
    return result


def get_reports(hours=24):
    return request_json("GET", f"{dashboard_url}/api/diagnosis?hours={hours}", timeout=30)


def parse_ts(value):
    if not value:
        return dt.datetime.fromtimestamp(0, dt.timezone.utc)
    value = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def plan_schema(report):
    plan = report.get("action_plan") or {}
    return plan.get("schema_version") or report.get("schema_version")


def plan_runners(report):
    plan = report.get("action_plan") or {}
    runners = []
    for step in plan.get("steps") or []:
        runner = step.get("runner") or {}
        argv = runner.get("argv") or []
        if argv:
            runners.append(" ".join(argv))
    return runners


def candidate_reports(started_at):
    reports = get_reports(hours=2)
    candidates = []
    for report in reports:
        status = report.get("plan_status")
        if status not in {"pending_approval", "approved", "queued"}:
            continue
        if plan_schema(report) != "3.0":
            continue
        if parse_ts(report.get("timestamp")) < started_at:
            continue
        text = json.dumps(report.get("action_plan") or {}, ensure_ascii=False).lower()
        text += " " + (report.get("summary") or "").lower()
        if "nginx" not in text:
            continue
        candidates.append(report)
    return candidates


def preferred_report(reports):
    restore = []
    other = []
    for report in reports:
        runners = " ".join(plan_runners(report))
        if "aads-nginx-restore-known-good" in runners or "nginx.restore_config" in runners:
            restore.append(report)
        else:
            other.append(report)
    return (restore or other)[0] if (restore or other) else None


def verify_latest(plan_id):
    if plan_id == "latest":
        reports = get_reports(hours=24)
        reports = [r for r in reports if r.get("plan_status") in {"kb_skipped", "final_verified", "kb_imported", "queued", "executing", "execution_failed", "blocked"}]
        if not reports:
            raise RuntimeError("no recent execution-like plan found")
        plan_id = reports[0]["diagnosis_id"]

    trace = request_json("GET", f"{dashboard_url}/api/plans/{plan_id}/execution", timeout=30)
    print(json.dumps(trace, indent=2, ensure_ascii=False)[:4000])
    target_command(["nginx", "-t"], as_root=True, operation="config_test", purpose="manual-demo-verify")
    target_command(["systemctl", "is-active", "nginx"], operation="status", purpose="manual-demo-verify")
    target_command(["curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1/"], operation="http_check", purpose="manual-demo-verify")
    log(f"verification complete for plan {plan_id}")
    return


def main():
    if verify_plan:
        verify_latest(verify_plan)
        return

    log(f"Dashboard: {dashboard_url}")
    log(f"Target agent: {target_url}")

    validate_lab_env()
    dashboard_json("POST", "/api/auth/check", timeout=15)
    facts = request_json(
        "GET",
        f"{target_url}/v1/node/facts",
        timeout=15,
        headers={"Authorization": f"Bearer {token}"},
    )
    caps = facts.get("runner_capabilities") or {}
    if caps.get("schema_version") != "runner.v1":
        raise RuntimeError(f"target is not advertising runner.v1: {caps}")
    if "supported_commands" in facts:
        raise RuntimeError("target returned legacy supported_commands")
    if facts.get("node_id") != expected_node_id:
        raise RuntimeError(f"target node mismatch: expected {expected_node_id}, got {facts.get('node_id')}")
    log(f"target node={facts.get('node_id')} runner={caps.get('schema_version')}")

    if reset_queue:
        stale = [
            r for r in get_reports(hours=24)
            if r.get("plan_status") in {"pending_approval", "approved"}
        ]
        for report in stale:
            plan_id = report["diagnosis_id"]
            dashboard_json("POST", f"/api/plans/{plan_id}/reject", body={"reason": "manual nginx demo reset"}, allow_error=True)
        log(f"rejected stale pending/approved plans: {len(stale)}")

    log("restoring nginx baseline from known-good snapshot")
    target_command(
        ["/usr/local/sbin/aads-nginx-restore-known-good"],
        as_root=True,
        side_effect="mutate",
        operation="restore_config",
        timeout=60,
    )
    target_command(["nginx", "-t"], as_root=True, operation="config_test")
    target_command(["systemctl", "is-active", "nginx"], operation="status")
    target_command(["curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1/"], operation="http_check")

    started_at = dt.datetime.now(dt.timezone.utc)
    marker = "manual-demo-nginx-bad-config-" + started_at.strftime("%Y%m%dT%H%M%S")
    log(f"injecting nginx bad config marker={marker}")
    target_command(
        ["cp", "/etc/nginx/nginx.conf", f"/etc/nginx/nginx.conf.{marker}.bak"],
        as_root=True,
        side_effect="mutate",
        operation="backup_config",
    )
    config_code = (
        "from pathlib import Path; "
        f"p=Path('/etc/nginx/nginx.conf'); p.write_text(p.read_text() + '\\n# {marker}\\nbroken {{\\n')"
    )
    log_code = (
        "from pathlib import Path; import datetime; "
        "p=Path('/var/log/nginx/error.log'); "
        f"p.open('a').write(datetime.datetime.now().isoformat() + ' [emerg] nginx: [emerg] invalid number of arguments in lab broken config {marker}\\n')"
    )
    target_command(["python3", "-c", config_code], as_root=True, side_effect="mutate", operation="inject_bad_config")
    target_command(["systemctl", "reload", "nginx"], as_root=True, side_effect="mutate", operation="reload_after_bad_config", ok_rc={0, 1})
    target_command(["python3", "-c", log_code], as_root=True, side_effect="mutate", operation="write_error_log")
    target_command(["nginx", "-t"], as_root=True, operation="config_test_after_inject", ok_rc={1})

    deadline = time.time() + wait_seconds
    latest_candidates = []
    while time.time() < deadline:
        latest_candidates = candidate_reports(started_at)
        chosen = preferred_report(latest_candidates)
        if chosen:
            runners = plan_runners(chosen)
            if any("aads-nginx-restore-known-good" in r or "nginx.restore_config" in r for r in runners):
                log("Dashboard queue is ready.")
                print()
                print("Recommended plan to approve/execute:")
                print(f"  diagnosis_id: {chosen['diagnosis_id']}")
                print(f"  status:       {chosen.get('plan_status')}")
                print(f"  summary:      {chosen.get('summary')}")
                print(f"  runner:       {', '.join(runners) if runners else '(none)'}")
                print()
                print("Open the Dashboard and manually click Approve, then Execute:")
                print(f"  {dashboard_url}")
                print()
                print("After execution, verify with:")
                print(f"  bash scripts/lab/demo-nginx-manual-gate.sh --verify {chosen['diagnosis_id']}")
                print()
                if len(latest_candidates) > 1:
                    print("Other candidate queue items were also generated. Prefer the restore plan above.")
                    for report in latest_candidates:
                        if report["diagnosis_id"] != chosen["diagnosis_id"]:
                            print(f"  - {report['diagnosis_id']}: {report.get('summary')} runners={plan_runners(report)}")
                return
        time.sleep(5)

    print("Timed out waiting for a restore queue item.", file=sys.stderr)
    if latest_candidates:
        print("Latest candidates:", file=sys.stderr)
        for report in latest_candidates:
            print(f"  - {report['diagnosis_id']}: {report.get('plan_status')} {report.get('summary')} runners={plan_runners(report)}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
PY
