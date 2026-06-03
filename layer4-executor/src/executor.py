"""
Knowledge Agent executor.

Consumes queued FixingPlan 3.0 executions, validates Gate/policy, calls the
On-Device Agent runner API (``POST /v1/commands/run``) one step at a time,
verifies each step by running a read-only runner and extracting structured
fields, and records append-only audit events. This process is deterministic and
intentionally imports no LLM client.

Removal note (legacy): this previously consumed FixingPlan 2.0 ``command_id``
steps and looked up catalog metadata (``scope`` / ``idempotent`` /
``retry_policy``) from node facts ``supported_commands``. The catalog is gone;
behaviour now comes from the plan's runner specs: ``runner.side_effect`` drives
locking, ``step.idempotency`` drives retry, ``verification.extract`` drives
structured verification, and ``plan.rollback.runner`` drives rollback.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("knowledge-agent")

SUPPORTED_PLAN_SCHEMA = "3.0"
POLL_INTERVAL = int(os.getenv("EXECUTOR_POLL_INTERVAL", "5"))
DEFAULT_LOCK_TTL_SECONDS = int(os.getenv("NODE_LOCK_TTL_SECONDS", "120"))
ON_DEVICE_AGENT_TOKEN = os.getenv("PI_AGENT_TOKEN", os.getenv("AADS_AGENT_TOKEN", ""))
ENABLE_KNOWLEDGE_BASE = os.getenv("ENABLE_KNOWLEDGE_BASE", "false").lower() == "true"

TERMINAL_STATUSES = {
    "final_verified",
    "kb_imported",
    "kb_skipped",
    "kb_import_failed",
    "execution_failed",
    "execution_failed_unknown_state",
    "blocked",
}

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "timescaledb"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "logdb"),
    "user": os.getenv("DB_USER", "logdb"),
    "password": os.getenv("DB_PASSWORD", "logdb_password"),
}


class Executor:
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None

    async def start(self):
        self.pool = await asyncpg.create_pool(**DB_CONFIG, min_size=1, max_size=5)
        logger.info("Knowledge Agent connected to DB")
        await self.sweep_expired_locks()
        while True:
            try:
                await self.sweep_expired_locks()
                processed = await self.process_one()
                if not processed:
                    await asyncio.sleep(POLL_INTERVAL)
            except Exception as e:
                logger.error("executor loop error: %s", e, exc_info=True)
                await asyncio.sleep(POLL_INTERVAL)

    async def process_one(self) -> bool:
        assert self.pool
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT execution_id, plan_id, idempotency_key, status
                    FROM plan_executions
                    WHERE status IN ('queued', 'executing', 'final_verifying')
                    ORDER BY requested_at ASC
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                    """
                )
                if not row:
                    return False
                if row["status"] == "queued":
                    await conn.execute(
                        """
                        UPDATE plan_executions
                        SET status = 'executing', started_at = COALESCE(started_at, NOW())
                        WHERE execution_id = $1
                        """,
                        row["execution_id"],
                    )
                    await conn.execute(
                        "UPDATE diagnosis_reports SET plan_status = 'executing' WHERE diagnosis_id = $1",
                        row["plan_id"],
                    )
                    await self.audit(
                        conn,
                        "execution.executing",
                        row["plan_id"],
                        None,
                        None,
                        SUPPORTED_PLAN_SCHEMA,
                        "allowed",
                        row["idempotency_key"],
                        0,
                        "executing",
                        {"execution_id": row["execution_id"]},
                    )
                else:
                    await self.audit(
                        conn,
                        "execution.recovery_started",
                        row["plan_id"],
                        None,
                        None,
                        SUPPORTED_PLAN_SCHEMA,
                        "allowed",
                        row["idempotency_key"],
                        0,
                        row["status"],
                        {"execution_id": row["execution_id"]},
                    )

        await self.execute_plan(row["execution_id"], row["plan_id"], row["idempotency_key"])
        return True

    async def execute_plan(self, execution_id: str, plan_id: str, idempotency_key: str):
        assert self.pool
        async with self.pool.acquire() as conn:
            diagnosis = await conn.fetchrow(
                "SELECT action_plan, schema_version, root_cause, summary FROM diagnosis_reports WHERE diagnosis_id = $1",
                plan_id,
            )
            if not diagnosis:
                await self.finish_execution(conn, execution_id, "blocked", {"reason": "missing_plan"})
                return

            plan = self._json(diagnosis["action_plan"])
            schema_version = plan.get("schema_version") or diagnosis["schema_version"] or "1.0"
            validation_error = self.validate_plan(plan, schema_version)
            if validation_error:
                await self.audit(conn, "policy.blocked", plan_id, None, None, schema_version, validation_error, idempotency_key, 0, "blocked", {})
                await self.finish_execution(conn, execution_id, "blocked", {"reason": validation_error})
                return

            target_node_id = plan["target_node_id"]
            if not await self.approval_valid(conn, plan_id) and not self.plan_auto_allowed(plan):
                await self.audit(
                    conn,
                    "policy.blocked",
                    plan_id,
                    None,
                    target_node_id,
                    schema_version,
                    "approval_required_or_expired",
                    idempotency_key,
                    0,
                    "blocked",
                    {},
                )
                await self.finish_execution(conn, execution_id, "blocked", {"reason": "approval_required_or_expired"})
                return

        node = await self.load_node(plan_id, target_node_id, idempotency_key)
        if not node:
            async with self.pool.acquire() as conn:
                await self.finish_execution(conn, execution_id, "failed_retryable", {"reason": "agent_unreachable_or_unknown"})
            return

        if not self.environment_allowed(node["environment"], plan.get("environment_policy") or {}):
            async with self.pool.acquire() as conn:
                await self.audit(conn, "policy.blocked", plan_id, None, target_node_id, schema_version, "environment_policy", idempotency_key, 0, "blocked", plan.get("environment_policy") or {})
                await self.finish_execution(conn, execution_id, "blocked", {"reason": "environment_policy"})
            return

        final_result: Dict[str, Any] = {"steps": []}
        any_mutating = any((step.get("runner") or {}).get("side_effect") == "mutate" for step in plan["steps"])

        if any_mutating and plan.get("pre_execution_snapshot", {}).get("enabled", False):
            snapshot_status, snapshot_result = await self.ensure_snapshot(execution_id, plan_id, idempotency_key, schema_version, plan, node)
            final_result["pre_execution_snapshot"] = snapshot_result
            if snapshot_status != "step_verified":
                async with self.pool.acquire() as conn:
                    await self.finish_execution(conn, execution_id, "blocked", {"reason": "snapshot_failed", **final_result})
                return

        resume_index = await self.recovery_start_index(execution_id, plan, node, idempotency_key)
        if resume_index == -1:
            async with self.pool.acquire() as conn:
                await self.finish_execution(conn, execution_id, "execution_failed_unknown_state", {"reason": "running_step_not_recoverable"})
            return

        for step in plan["steps"][resume_index:]:
            step_status, step_result = await self.execute_fixing_step(
                execution_id,
                plan_id,
                idempotency_key,
                schema_version,
                plan,
                step,
                node,
            )
            final_result["steps"].append({"step_id": step["step_id"], "status": step_status, "result": step_result})
            if step_status != "step_verified":
                if step_status == "step_failed_aborted" and (plan.get("rollback") or {}).get("enabled", False):
                    rollback_status, rollback_result = await self.rollback(execution_id, plan_id, idempotency_key, schema_version, plan, node)
                    final_result["rollback"] = {"status": rollback_status, "result": rollback_result}
                async with self.pool.acquire() as conn:
                    terminal = "blocked" if step_status == "step_failed_blocked" else "execution_failed"
                    await self.finish_execution(conn, execution_id, terminal, final_result)
                return

        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE plan_executions SET status = 'final_verifying' WHERE execution_id = $1", execution_id)
            await conn.execute("UPDATE diagnosis_reports SET plan_status = 'final_verifying' WHERE diagnosis_id = $1", plan_id)
            await self.audit(conn, "execution.final_verifying", plan_id, None, target_node_id, schema_version, "allowed", idempotency_key, 0, "final_verifying", {})

        final_status, final_payload = await self.run_verification(plan["final_verification"], plan_id, None, idempotency_key, schema_version, node, event_prefix="final_verification", execution_id=None)
        final_result["final_verification"] = final_payload
        if final_status != "step_verified":
            async with self.pool.acquire() as conn:
                await self.finish_execution(conn, execution_id, "execution_failed", final_result)
            return

        async with self.pool.acquire() as conn:
            await self.audit(conn, "execution.final_verified", plan_id, None, target_node_id, schema_version, "allowed", idempotency_key, 0, "final_verified", final_payload)
            await conn.execute("UPDATE plan_executions SET status = 'final_verified' WHERE execution_id = $1", execution_id)
            await conn.execute("UPDATE diagnosis_reports SET plan_status = 'final_verified' WHERE diagnosis_id = $1", plan_id)
            kb_status, kb_result = await self.import_knowledge_case(conn, plan_id, diagnosis, plan, final_result)
            final_result["knowledge_base"] = kb_result
            await self.finish_execution(conn, execution_id, kb_status, final_result)

    async def execute_fixing_step(
        self,
        execution_id: str,
        plan_id: str,
        idempotency_key: str,
        schema_version: str,
        plan: Dict[str, Any],
        step: Dict[str, Any],
        node,
    ) -> Tuple[str, Dict[str, Any]]:
        step_label = self.step_label(step)
        status, command_result = await self.run_runner_command(
            plan_id,
            step["step_id"],
            idempotency_key,
            schema_version,
            node,
            step["runner"],
            step.get("context") or {},
            step.get("idempotency") or {},
            execution_id,
        )
        if status == "blocked":
            async with self.pool.acquire() as conn:
                await self.upsert_step(conn, execution_id, step["step_id"], "step_failed_blocked", step_label, node["node_id"], idempotency_key, command_result.get("retry_count", 0), command_result)
            return "step_failed_blocked", command_result
        if status != "success":
            async with self.pool.acquire() as conn:
                await self.upsert_step(conn, execution_id, step["step_id"], "step_failed_aborted", step_label, node["node_id"], idempotency_key, command_result.get("retry_count", 0), command_result)
            return "step_failed_aborted", command_result

        verification_status, verification_result = await self.run_verification(
            step["verification"],
            plan_id,
            step["step_id"],
            idempotency_key,
            schema_version,
            node,
            event_prefix="step_verification",
            execution_id=execution_id,
        )
        result = {"command": command_result, "verification": verification_result}
        if verification_status == "step_verified":
            async with self.pool.acquire() as conn:
                await self.upsert_step(conn, execution_id, step["step_id"], "step_verified", step_label, node["node_id"], idempotency_key, command_result.get("retry_count", 0), result)
            return "step_verified", result

        async with self.pool.acquire() as conn:
            await self.upsert_step(conn, execution_id, step["step_id"], "step_failed_aborted", step_label, node["node_id"], idempotency_key, command_result.get("retry_count", 0), result)
        return "step_failed_aborted", result

    @staticmethod
    def step_label(step: Dict[str, Any]) -> str:
        """Human/audit label for a runner step (replaces legacy command_id)."""
        context = step.get("context") or {}
        service = context.get("service")
        operation = context.get("operation")
        if service and operation:
            return f"{service}.{operation}"
        argv = (step.get("runner") or {}).get("argv") or []
        return argv[0] if argv else "runner"

    async def run_runner_command(
        self,
        plan_id: str,
        step_id: Optional[int],
        idempotency_key: str,
        schema_version: str,
        node,
        runner: Dict[str, Any],
        context: Dict[str, Any],
        idempotency: Dict[str, Any],
        execution_id: str,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Execute one runner spec via the agent's ``/v1/commands/run``.

        ``runner.side_effect == "mutate"`` is what acquires the node lock — not
        ``as_root``. Retry count comes from ``idempotency``, not catalog metadata.
        The agent returns HTTP 200 with a structured body even when the command
        exits non-zero, so the body (returncode/stdout/stderr) is always returned
        to the caller for extraction/verification.
        """
        base_url = node["base_url"].rstrip("/")
        node_id = node["node_id"]
        label = context.get("service", "") and f"{context.get('service')}.{context.get('operation')}" or (runner.get("argv") or ["runner"])[0]
        mutating = runner.get("side_effect") == "mutate"
        timeout = int(runner.get("timeout_seconds", 30))
        attempts = self.max_attempts(idempotency)
        lock_id = None

        if mutating:
            async with self.pool.acquire() as conn:
                lock_id = await self.acquire_lock(conn, node_id, plan_id, execution_id, timeout)
                if not lock_id:
                    await self.audit(conn, "policy.blocked", plan_id, step_id, node_id, schema_version, "node_locked", idempotency_key, 0, "blocked", {})
                    return "blocked", {"reason": "node_locked", "retry_count": 0}

        body = {"schema_version": "runner.v1", "runner": runner, "context": context, "idempotency": idempotency}
        try:
            last_payload: Dict[str, Any] = {}
            for attempt in range(1, attempts + 1):
                retry_count = attempt - 1
                async with self.pool.acquire() as conn:
                    if step_id is not None:
                        await self.upsert_step(conn, execution_id, step_id, "step_running", label, node_id, idempotency_key, retry_count)
                    await self.audit(conn, "execution.attempt", plan_id, step_id, node_id, schema_version, "allowed", idempotency_key, retry_count, "step_running", {"label": label, "as_root": runner.get("as_root", False), "side_effect": runner.get("side_effect", "read")})

                transport, payload = await self.call_agent(base_url, "POST", "/v1/commands/run", body, timeout)
                if transport != "success":
                    # HTTP/transport error (no structured body); retry if attempts remain.
                    payload["retry_count"] = retry_count
                    last_payload = payload
                    if attempt < attempts:
                        await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.retrying", idempotency_key, retry_count, "step_failed_retried", payload)
                        continue
                    await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.command_failed", idempotency_key, retry_count, "failed_retryable", payload)
                    return "error", payload

                payload["retry_count"] = retry_count
                last_payload = payload
                run_status = payload.get("status")
                if run_status == "blocked":
                    await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.command_blocked", idempotency_key, retry_count, "blocked", payload)
                    return "blocked", payload
                if run_status == "success":
                    await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.command_success", idempotency_key, retry_count, "success", payload)
                    return "success", payload
                # run_status in (failed, timeout): the command ran but exited non-zero.
                if attempt < attempts:
                    await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.retrying", idempotency_key, retry_count, "step_failed_retried", payload)

            await self.audit_event(plan_id, step_id, node_id, schema_version, "execution.command_failed", idempotency_key, attempts - 1, "failed_retryable", last_payload)
            return "failed_retryable", last_payload
        finally:
            if lock_id:
                async with self.pool.acquire() as conn:
                    await conn.execute("DELETE FROM node_locks WHERE node_id = $1 AND lock_id = $2", node_id, lock_id)

    async def run_verification(
        self,
        verification: Dict[str, Any],
        plan_id: str,
        step_id: Optional[int],
        idempotency_key: str,
        schema_version: str,
        node,
        event_prefix: str,
        execution_id: Optional[str],
    ) -> Tuple[str, Dict[str, Any]]:
        runner = verification.get("runner") or {}
        if not runner.get("argv"):
            payload = {"reason": "verification_runner_invalid"}
            await self.audit_event(plan_id, step_id, node["node_id"], schema_version, f"{event_prefix}.blocked", idempotency_key, 0, "blocked", payload)
            return "step_failed_blocked", payload
        if runner.get("side_effect", "read") != "read":
            payload = {"reason": "verification_runner_not_read"}
            await self.audit_event(plan_id, step_id, node["node_id"], schema_version, f"{event_prefix}.blocked", idempotency_key, 0, "blocked", payload)
            return "step_failed_blocked", payload

        # Verification never retries on a non-zero exit: a probe that "fails" by
        # design (e.g. is-active -> inactive) must still yield its output so we can
        # extract structured fields and decide via matches_expected.
        status, payload = await self.run_runner_command(
            plan_id,
            step_id,
            idempotency_key,
            schema_version,
            node,
            runner,
            verification.get("context") or {"purpose": "verify", **{k: v for k, v in (verification.get("context") or {}).items()}},
            {"mode": "idempotent", "max_attempts": 1},
            execution_id or f"verify_{plan_id}_{step_id or 0}",
        )
        if status == "blocked":
            await self.audit_event(plan_id, step_id, node["node_id"], schema_version, f"{event_prefix}.blocked", idempotency_key, payload.get("retry_count", 0), "blocked", payload)
            return "step_failed_blocked", payload
        if status == "error":
            await self.audit_event(plan_id, step_id, node["node_id"], schema_version, f"{event_prefix}.failed", idempotency_key, payload.get("retry_count", 0), "failed", payload)
            return "step_failed_aborted", payload

        observed = self.apply_extractors(payload, verification.get("extract") or {})
        match, mismatches = self.matches_expected(observed, verification.get("expected") or {})
        result = {
            "status": "success" if match else "failed",
            "observed": observed,
            "expected": verification.get("expected") or {},
            "mismatches": mismatches,
            "raw": {"returncode": payload.get("returncode"), "stdout": payload.get("stdout"), "stderr": payload.get("stderr")},
        }
        await self.audit_event(
            plan_id,
            step_id,
            node["node_id"],
            schema_version,
            f"{event_prefix}.{'verified' if match else 'failed'}",
            idempotency_key,
            payload.get("retry_count", 0),
            "step_verified" if match else "step_failed_aborted",
            result,
        )
        return ("step_verified", result) if match else ("step_failed_aborted", result)

    def apply_extractors(self, result: Dict[str, Any], extract_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Turn a runner result (returncode/stdout/stderr) into a structured
        ``observed`` dict per the declarative extract rules. This is the trust
        boundary for verification: only these five sources are readable, and the
        op decides the value type (bool/int/value/string).
        """
        observed: Dict[str, Any] = {}
        for key, rule in (extract_spec or {}).items():
            source = rule.get("from", rule.get("from_"))
            if source == "returncode":
                text = "" if result.get("returncode") is None else str(result.get("returncode"))
            elif source == "stdout":
                text = result.get("stdout", "") or ""
            elif source == "stderr":
                text = result.get("stderr", "") or ""
            elif source == "stdout_stripped":
                text = (result.get("stdout", "") or "").strip()
            elif source == "stderr_stripped":
                text = (result.get("stderr", "") or "").strip()
            else:
                text = ""

            if rule.get("equals") is not None:
                observed[key] = text == rule["equals"]
            elif rule.get("contains") is not None:
                observed[key] = rule["contains"] in text
            elif rule.get("regex") is not None:
                observed[key] = bool(re.search(rule["regex"], text))
            elif rule.get("as_int"):
                try:
                    observed[key] = int(text.strip())
                except (ValueError, TypeError):
                    observed[key] = None
            elif rule.get("json_path"):
                observed[key] = self._json_path_value(text, rule["json_path"])
            elif source == "returncode":
                observed[key] = result.get("returncode")
            else:
                observed[key] = text
        return observed

    @staticmethod
    def _json_path_value(text: str, path: str) -> Any:
        try:
            data: Any = json.loads(text)
        except (ValueError, TypeError):
            return None
        for part in str(path).split("."):
            if isinstance(data, dict) and part in data:
                data = data[part]
            else:
                return None
        return data

    async def ensure_snapshot(self, execution_id: str, plan_id: str, idempotency_key: str, schema_version: str, plan: Dict[str, Any], node):
        spec = plan.get("pre_execution_snapshot") or {}
        runner = spec.get("runner") or {}
        label = f"{(spec.get('scope') or 'snapshot')}.ensure_snapshot"
        if not runner.get("argv"):
            return "step_failed_blocked", {"reason": "snapshot_runner_invalid"}
        status, result = await self.run_runner_command(plan_id, 0, idempotency_key, schema_version, node, runner, {"purpose": "snapshot", "operation": "ensure_snapshot"}, spec.get("idempotency") or {"mode": "idempotent", "max_attempts": 2}, execution_id)
        if status == "success":
            async with self.pool.acquire() as conn:
                await self.upsert_step(conn, execution_id, 0, "step_verified", label, node["node_id"], idempotency_key, result.get("retry_count", 0), result)
            await self.audit_event(plan_id, 0, node["node_id"], schema_version, "snapshot.ready", idempotency_key, result.get("retry_count", 0), "step_verified", result)
            return "step_verified", result
        step_status = "step_failed_blocked" if status == "blocked" else "step_failed_aborted"
        async with self.pool.acquire() as conn:
            await self.upsert_step(conn, execution_id, 0, step_status, label, node["node_id"], idempotency_key, result.get("retry_count", 0), result)
        await self.audit_event(plan_id, 0, node["node_id"], schema_version, "snapshot.failed", idempotency_key, result.get("retry_count", 0), step_status, result)
        return status, result

    async def rollback(self, execution_id: str, plan_id: str, idempotency_key: str, schema_version: str, plan: Dict[str, Any], node):
        spec = plan.get("rollback") or {}
        runner = spec.get("runner") or {}
        if not spec.get("enabled") or not runner.get("argv"):
            return "rollback_skipped", {"reason": "rollback_runner_not_configured"}
        await self.audit_event(plan_id, None, node["node_id"], schema_version, "rollback.running", idempotency_key, 0, "rollback_running", {})
        status, result = await self.run_runner_command(plan_id, None, idempotency_key, schema_version, node, runner, {"purpose": "rollback", "operation": "restore_config"}, {"mode": "idempotent", "max_attempts": 1}, execution_id)
        rollback_status = "rollback_completed" if status == "success" else "rollback_failed"
        await self.audit_event(plan_id, None, node["node_id"], schema_version, f"rollback.{rollback_status}", idempotency_key, result.get("retry_count", 0), rollback_status, result)
        return rollback_status, result

    async def recovery_start_index(self, execution_id: str, plan: Dict[str, Any], node, idempotency_key: str) -> int:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT step_id, status, command_id, retry_count
                FROM execution_steps
                WHERE execution_id = $1 AND step_id > 0
                ORDER BY step_id
                """,
                execution_id,
            )
        by_step = {row["step_id"]: row for row in rows}
        for index, step in enumerate(plan["steps"]):
            row = by_step.get(step["step_id"])
            if not row:
                return index
            if row["status"] == "step_verified":
                continue
            if row["status"] == "step_running":
                verification_status, _ = await self.run_verification(step["verification"], plan["plan_id"], step["step_id"], idempotency_key, plan["schema_version"], node, "recovery_verification", execution_id=execution_id)
                if verification_status == "step_verified":
                    async with self.pool.acquire() as conn:
                        await self.upsert_step(conn, execution_id, step["step_id"], "step_verified", self.step_label(step), node["node_id"], idempotency_key, row["retry_count"], {"recovered": True})
                    continue
                idempotency = step.get("idempotency") or {}
                if idempotency.get("mode") == "idempotent" and row["retry_count"] < self.max_attempts(idempotency) - 1:
                    return index
                return -1
            return -1
        return len(plan["steps"])

    async def load_node(self, plan_id: str, target_node_id: str, idempotency_key: str):
        assert self.pool
        async with self.pool.acquire() as conn:
            node = await conn.fetchrow("SELECT * FROM agent_nodes WHERE node_id = $1", target_node_id)
            if not node:
                await self.audit(conn, "policy.blocked", plan_id, None, target_node_id, SUPPORTED_PLAN_SCHEMA, "unknown_node", idempotency_key, 0, "blocked", {})
                return None
        # Reachability check only — the catalog is gone, facts no longer gate commands.
        status, facts = await self.call_agent(node["base_url"].rstrip("/"), "GET", "/v1/node/facts", None, 10)
        if status != "success":
            await self.audit_event(plan_id, None, target_node_id, SUPPORTED_PLAN_SCHEMA, "agent.unreachable", idempotency_key, 0, "failed_retryable", facts)
            return None
        return node

    def validate_plan(self, plan: Dict[str, Any], schema_version: str) -> Optional[str]:
        if schema_version != SUPPORTED_PLAN_SCHEMA or plan.get("schema_version") != SUPPORTED_PLAN_SCHEMA:
            return "unsupported_schema"
        required = ["plan_id", "rca_report_id", "target_node_id", "goal", "risk_level", "environment_policy", "pre_execution_snapshot", "steps", "final_verification", "self_check"]
        for field in required:
            if field not in plan:
                return f"missing_{field}"
        if not (plan.get("self_check") or {}).get("passed"):
            return "self_check_failed"
        steps = plan.get("steps")
        if not isinstance(steps, list) or not steps:
            return "no_steps"
        orders = [step.get("order") for step in steps]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            return "invalid_step_order"
        for step in steps:
            for field in ["step_id", "order", "runner", "expected_outcome", "on_failure", "verification"]:
                if field not in step:
                    return f"missing_step_{field}"
            if step["on_failure"] not in ("abort", "rollback"):
                return "invalid_on_failure"
            reason = self.validate_runner(step.get("runner"), require_read=False)
            if reason:
                return reason
            reason = self.validate_verification(step["verification"])
            if reason:
                return reason
        return self.validate_verification(plan["final_verification"])

    def validate_runner(self, runner: Optional[Dict[str, Any]], require_read: bool) -> Optional[str]:
        if not isinstance(runner, dict):
            return "missing_runner"
        argv = runner.get("argv")
        if not isinstance(argv, list) or not argv or any((not isinstance(a, str) or a == "") for a in argv):
            return "invalid_runner_argv"
        if runner.get("mode", "argv") != "argv":
            return "unsupported_runner_mode"
        if runner.get("side_effect", "read") not in ("read", "mutate"):
            return "invalid_side_effect"
        if require_read and runner.get("side_effect", "read") != "read":
            return "verification_runner_not_read"
        return None

    def validate_verification(self, verification: Dict[str, Any]) -> Optional[str]:
        if verification.get("type", "runner_probe") != "runner_probe":
            return "invalid_verification_type"
        reason = self.validate_runner(verification.get("runner"), require_read=True)
        if reason:
            return reason
        expected = verification.get("expected")
        if not isinstance(expected, dict) or not expected:
            return "invalid_structured_verification"
        if any(key in expected for key in {"text", "prompt", "llm_judge"}):
            return "free_text_verification_not_allowed"
        extract = verification.get("extract") or {}
        missing = set(expected.keys()) - set(extract.keys())
        if missing:
            return "extract_missing_expected_keys"
        return None

    def environment_allowed(self, node_environment: str, env_policy: Dict[str, Any]) -> bool:
        expected = env_policy.get("environment")
        if expected and expected != node_environment:
            return False
        if node_environment == "prod" and env_policy.get("auto_execute_allowed") is True:
            return False
        return True

    def plan_auto_allowed(self, plan: Dict[str, Any]) -> bool:
        policy = plan.get("environment_policy") or {}
        return (
            plan.get("risk_level") == "low"
            and policy.get("environment") == "test"
            and policy.get("auto_execute_allowed") is True
        )

    def max_attempts(self, idempotency: Dict[str, Any]) -> int:
        idempotency = idempotency or {}
        if idempotency.get("mode", "idempotent") != "idempotent":
            return 1
        return max(1, int(idempotency.get("max_attempts", 2)))

    def matches_expected(self, observed: Dict[str, Any], expected: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
        mismatches = []
        for key, expected_value in expected.items():
            observed_value = observed.get(key)
            if observed_value != expected_value:
                mismatches.append({"field": key, "expected": expected_value, "observed": observed_value})
        return not mismatches, mismatches

    async def call_agent(self, base_url: str, method: str, path: str, body: Optional[Dict[str, Any]], timeout: int):
        headers = {"Authorization": f"Bearer {ON_DEVICE_AGENT_TOKEN}"}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                request = session.get if method == "GET" else session.post
                kwargs: Dict[str, Any] = {"timeout": aiohttp.ClientTimeout(total=timeout)}
                if body is not None:
                    kwargs["json"] = body
                async with request(f"{base_url}{path}", **kwargs) as response:
                    payload = await response.json(content_type=None)
                    if 200 <= response.status < 300:
                        return "success", payload
                    if 400 <= response.status < 500:
                        return "blocked", {"status": response.status, "payload": payload, "retryable": False}
                    return "error", {"status": response.status, "payload": payload, "retryable": response.status >= 500}
        except Exception as e:
            return "error", {"error": str(e), "retryable": True}

    async def acquire_lock(self, conn, node_id: str, plan_id: str, execution_id: str, timeout_seconds: int) -> Optional[str]:
        lock_id = str(uuid.uuid4())
        ttl = max(timeout_seconds * 2, DEFAULT_LOCK_TTL_SECONDS)
        row = await conn.fetchrow(
            """
            INSERT INTO node_locks (node_id, lock_id, plan_id, execution_id, expires_at)
            VALUES ($1, $2, $3, $4, NOW() + $5 * INTERVAL '1 second')
            ON CONFLICT (node_id) DO UPDATE SET
                lock_id = EXCLUDED.lock_id,
                plan_id = EXCLUDED.plan_id,
                execution_id = EXCLUDED.execution_id,
                expires_at = EXCLUDED.expires_at,
                created_at = NOW()
            WHERE node_locks.expires_at < NOW()
            RETURNING lock_id
            """,
            node_id,
            lock_id,
            plan_id,
            execution_id,
            ttl,
        )
        return row["lock_id"] if row else None

    async def sweep_expired_locks(self):
        assert self.pool
        async with self.pool.acquire() as conn:
            deleted = await conn.execute("DELETE FROM node_locks WHERE expires_at < NOW()")
            if deleted != "DELETE 0":
                logger.info("swept expired locks: %s", deleted)

    async def approval_valid(self, conn, plan_id: str) -> bool:
        row = await conn.fetchrow(
            """
            SELECT decision, approved_until
            FROM plan_approvals
            WHERE plan_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            plan_id,
        )
        if not row or row["decision"] != "approved" or not row["approved_until"]:
            return False
        approved_until = row["approved_until"]
        if approved_until.tzinfo is None:
            approved_until = approved_until.replace(tzinfo=timezone.utc)
        return approved_until > datetime.now(timezone.utc)

    async def upsert_step(
        self,
        conn,
        execution_id: str,
        step_id: int,
        status: str,
        command_id: str,
        node_id: str,
        idempotency_key: str,
        retry_count: int,
        result: Optional[Dict[str, Any]] = None,
    ):
        await conn.execute(
            """
            INSERT INTO execution_steps
            (execution_id, step_id, status, command_id, target_node_id,
             idempotency_key, retry_count, started_at, finished_at, result)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(),
                    CASE WHEN $3 IN ('step_verified', 'step_failed_blocked',
                                     'step_failed_aborted', 'step_failed_retried')
                         THEN NOW() ELSE NULL END,
                    $8)
            ON CONFLICT (execution_id, step_id) DO UPDATE SET
                status = EXCLUDED.status,
                command_id = EXCLUDED.command_id,
                target_node_id = EXCLUDED.target_node_id,
                idempotency_key = EXCLUDED.idempotency_key,
                retry_count = EXCLUDED.retry_count,
                finished_at = EXCLUDED.finished_at,
                result = EXCLUDED.result
            """,
            execution_id,
            step_id,
            status,
            command_id,
            node_id,
            idempotency_key,
            retry_count,
            json.dumps(result or {}),
        )

    async def import_knowledge_case(self, conn, plan_id: str, diagnosis, plan: Dict[str, Any], final_result: Dict[str, Any]):
        if not ENABLE_KNOWLEDGE_BASE:
            await self.audit(conn, "kb.skipped", plan_id, None, plan["target_node_id"], SUPPORTED_PLAN_SCHEMA, "allowed", None, 0, "kb_skipped", {"reason": "ENABLE_KNOWLEDGE_BASE=false"})
            return "kb_skipped", {"status": "kb_skipped", "reason": "ENABLE_KNOWLEDGE_BASE=false"}
        try:
            root_cause = self._json(diagnosis["root_cause"])
            await conn.execute(
                """
                INSERT INTO knowledge_cases
                (case_id, anomaly_pattern, diagnosis_summary, root_cause, resolution, effectiveness)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (case_id) DO UPDATE SET
                    diagnosis_summary = EXCLUDED.diagnosis_summary,
                    root_cause = EXCLUDED.root_cause,
                    resolution = EXCLUDED.resolution,
                    effectiveness = EXCLUDED.effectiveness,
                    updated_at = NOW()
                """,
                f"case_{plan_id}",
                plan.get("goal", ""),
                diagnosis["summary"],
                root_cause.get("description", ""),
                json.dumps(final_result),
                1.0,
            )
            await self.audit(conn, "kb.imported", plan_id, None, plan["target_node_id"], SUPPORTED_PLAN_SCHEMA, "allowed", None, 0, "kb_imported", {})
            return "kb_imported", {"status": "kb_imported", "case_id": f"case_{plan_id}"}
        except Exception as e:
            await self.audit(conn, "kb.import_failed", plan_id, None, plan["target_node_id"], SUPPORTED_PLAN_SCHEMA, "allowed", None, 0, "kb_import_failed", {"error": str(e)})
            return "kb_import_failed", {"status": "kb_import_failed", "error": str(e)}

    async def finish_execution(self, conn, execution_id: str, status: str, result: Dict[str, Any]):
        execution = await conn.fetchrow(
            """
            SELECT plan_id, idempotency_key, schema_version, target_node_id
            FROM plan_executions
            WHERE execution_id = $1
            """,
            execution_id,
        )
        await conn.execute(
            """
            UPDATE plan_executions
            SET status = $2, finished_at = NOW(), result = $3
            WHERE execution_id = $1
            """,
            execution_id,
            status,
            json.dumps(result),
        )
        await conn.execute(
            """
            UPDATE diagnosis_reports
            SET plan_status = $2
            WHERE diagnosis_id = (
                SELECT plan_id FROM plan_executions WHERE execution_id = $1
            )
            """,
            execution_id,
            status,
        )
        if execution:
            await self.audit(
                conn,
                "execution.finished",
                execution["plan_id"],
                None,
                execution["target_node_id"],
                execution["schema_version"],
                "allowed",
                execution["idempotency_key"],
                0,
                status,
                {"execution_id": execution_id, "result": result},
            )

    async def audit_event(self, plan_id: str, step_id: Optional[int], node_id: Optional[str], schema_version: str, event_type: str, idempotency_key: Optional[str], retry_count: int, result: str, metadata: Dict[str, Any]):
        async with self.pool.acquire() as conn:
            await self.audit(conn, event_type, plan_id, step_id, node_id, schema_version, "allowed", idempotency_key, retry_count, result, metadata)

    async def audit(
        self,
        conn,
        event_type: str,
        plan_id: Optional[str],
        step_id: Optional[int],
        node_id: Optional[str],
        schema_version: Optional[str],
        policy_decision: Optional[str],
        idempotency_key: Optional[str],
        retry_count: int,
        result: str,
        metadata: Dict[str, Any],
    ):
        await conn.execute(
            """
            INSERT INTO audit_events
            (actor, event_type, plan_id, step_id, node_id, schema_version,
             policy_decision, idempotency_key, retry_count, result, metadata)
            VALUES ('knowledge-agent', $1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
            event_type,
            plan_id,
            step_id,
            node_id,
            schema_version,
            policy_decision,
            idempotency_key,
            retry_count,
            result,
            json.dumps(metadata),
        )

    def _json(self, value: Any) -> Dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)


async def main():
    await Executor().start()


if __name__ == "__main__":
    asyncio.run(main())
