"""
Simple Dashboard for AI Auto-Debug System
Display anomalies and diagnosis reports
"""
from flask import Flask, render_template, jsonify, request, send_from_directory
import asyncpg
import asyncio
import hashlib
import os
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# Database configuration
DB_HOST = os.getenv('DB_HOST', 'timescaledb')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'logdb')
DB_USER = os.getenv('DB_USER', 'logdb')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'logdb_password')
ADMIN_API_KEY = os.getenv('AADS_ADMIN_API_KEY', 'change-me-admin-key')
APPROVAL_EXPIRY_MINUTES = int(os.getenv('APPROVAL_EXPIRY_MINUTES', '30'))
SUPPORTED_EXECUTION_SCHEMA = '3.1'
SUPPORTED_EXECUTION_SCHEMAS = frozenset(['3.0', '3.1'])
IDEMPOTENCY_TTL_MINUTES = int(os.getenv('AADS_IDEMPOTENCY_TTL_MINUTES', '30'))


def plan_sha256(plan):
    """Canonical plan hash binding an approval to exact plan content (ADR-006).

    Must stay byte-identical to Executor.plan_sha256 in layer4."""
    canonical = json.dumps(plan, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


async def get_db_connection():
    """Create database connection"""
    return await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def normalize_admin_key(value):
    key = (value or '').strip()
    match = re.search(r'AADS_ADMIN_API_KEY\s*=\s*([^\s#]+)', key)
    if match:
        key = match.group(1)
    if key.startswith('export '):
        key = key[len('export '):].strip()
    if key.startswith('AADS_ADMIN_API_KEY='):
        key = key.split('=', 1)[1].strip()
    return key.strip('"\'')


def key_diagnostics(key):
    if not key:
        return {'present': False, 'length': 0, 'fingerprint': None}
    return {
        'present': True,
        'length': len(key),
        'fingerprint': hashlib.sha256(key.encode()).hexdigest()[:10],
    }


def require_admin():
    key = normalize_admin_key(request.headers.get('X-Admin-API-Key'))
    if key != ADMIN_API_KEY:
        app.logger.warning(
            "Admin auth failed: received=%s expected=%s",
            key_diagnostics(key),
            key_diagnostics(ADMIN_API_KEY),
        )
        return jsonify({
            'error': 'unauthorized',
            'received': key_diagnostics(key),
            'expected': {
                'present': bool(ADMIN_API_KEY),
                'length': len(ADMIN_API_KEY),
            },
        }), 401
    return None


@app.route('/api/auth/check', methods=['POST'])
def api_auth_check():
    auth = require_admin()
    if auth:
        return auth
    return jsonify({'status': 'ok', 'key': key_diagnostics(normalize_admin_key(request.headers.get('X-Admin-API-Key')))})


def parse_jsonb(value):
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


RESOLVED_STATUSES = frozenset([
    'final_verified', 'kb_imported', 'kb_skipped', 'kb_import_failed', 'rejected',
])

async def get_diagnosis_reports(hours=168):
    """Get recent diagnosis reports.

    Active/attention items are filtered by the hours window so the queue stays
    focused on recent work.  Resolved items (Repair History) are always returned
    regardless of age — audit trails should never disappear just because a
    time-picker moved.
    """
    conn = await get_db_connection()
    try:
        resolved_list = ", ".join(f"'{s}'" for s in RESOLVED_STATUSES)
        rows = await conn.fetch(
            f"""
            SELECT diagnosis_id, timestamp, severity, summary,
                   root_cause, recommended_actions, action_plan,
                   schema_version, plan_status
            FROM diagnosis_reports
            WHERE timestamp > NOW() - INTERVAL '{hours} hours'
               OR plan_status IN ({resolved_list})
            ORDER BY timestamp DESC
            LIMIT 200
            """
        )

        reports = []
        for row in rows:
            # Handle action_plan (can be None for old records)
            action_plan = None
            if row['action_plan'] is not None:
                action_plan = parse_jsonb(row['action_plan'])

            reports.append({
                'diagnosis_id': row['diagnosis_id'],
                'timestamp': row['timestamp'].isoformat(),
                'severity': row['severity'],
                'summary': row['summary'],
                'root_cause': parse_jsonb(row['root_cause']),
                'recommended_actions': parse_jsonb(row['recommended_actions']),
                'action_plan': action_plan,
                'schema_version': row['schema_version'],
                'plan_status': row['plan_status'],
            })

        return reports
    finally:
        await conn.close()


async def get_anomaly_stats(hours=168):
    """Get anomaly statistics for the given window."""
    conn = await get_db_connection()
    try:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM anomaly_logs WHERE time > NOW() - INTERVAL '%s hours'" % hours
        )
        by_container = await conn.fetch(
            """
            SELECT container, COUNT(*) as count,
                   AVG(anomaly_score) as avg_score
            FROM anomaly_logs
            WHERE time > NOW() - INTERVAL '%s hours'
            GROUP BY container
            ORDER BY count DESC
            LIMIT 10
            """ % hours
        )
        by_filter = await conn.fetch(
            """
            SELECT filter_stage, COUNT(*) as count
            FROM anomaly_logs
            WHERE time > NOW() - INTERVAL '%s hours'
            GROUP BY filter_stage
            """ % hours
        )
        return {
            'total': total,
            'by_container': [dict(row) for row in by_container],
            'by_filter': [dict(row) for row in by_filter]
        }
    finally:
        await conn.close()


async def get_diagnosis_stats(hours=168):
    """Get diagnosis statistics.

    Totals reflect the time window for the instrument rail.
    Resolved (history) count is always all-time so History tab stays accurate.
    """
    conn = await get_db_connection()
    try:
        resolved_list = ", ".join(f"'{s}'" for s in RESOLVED_STATUSES)
        by_severity = await conn.fetch(
            f"""
            SELECT severity, COUNT(*) as count
            FROM diagnosis_reports
            WHERE timestamp > NOW() - INTERVAL '{hours} hours'
               OR plan_status IN ({resolved_list})
            GROUP BY severity
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
            """
        )
        total = await conn.fetchval(
            f"""
            SELECT COUNT(*) FROM diagnosis_reports
            WHERE timestamp > NOW() - INTERVAL '{hours} hours'
               OR plan_status IN ({resolved_list})
            """
        )
        return {
            'total': total,
            'by_severity': [dict(row) for row in by_severity]
        }
    finally:
        await conn.close()


async def get_anomaly_timeline(hours=24):
    """Get hourly anomaly counts for timeline chart"""
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT
                time_bucket('1 hour', time) AS bucket,
                COUNT(*) as count
            FROM anomaly_logs
            WHERE time > NOW() - INTERVAL '%s hours'
            GROUP BY bucket
            ORDER BY bucket
            """ % hours
        )
        return [
            {
                'timestamp': row['bucket'].isoformat(),
                'count': row['count']
            }
            for row in rows
        ]
    finally:
        await conn.close()


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')


# Self-serve On-Device Agent installer. The bootstrap script drops
# install-agent.sh + aads-agent.tgz into AADS_DIST_DIR (mounted from ./dist),
# so targets can `curl -fsSL http://<server>:5000/install-agent.sh | sudo bash`
# without any external download source.
DIST_DIR = os.getenv('AADS_DIST_DIR', '/app/dist')


@app.route('/install-agent.sh')
def serve_install_agent():
    return send_from_directory(DIST_DIR, 'install-agent.sh', mimetype='text/x-shellscript')


@app.route('/aads-agent.tgz')
def serve_agent_payload():
    return send_from_directory(DIST_DIR, 'aads-agent.tgz', mimetype='application/gzip')


@app.route('/api/diagnosis')
def api_diagnosis():
    """API endpoint for diagnosis reports"""
    hours = int(request.args.get("hours", 168))
    reports = run_async(get_diagnosis_reports(hours))
    return jsonify(reports)


@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    hours = int(request.args.get("hours", 168))
    anomaly_stats = run_async(get_anomaly_stats(hours))
    diagnosis_stats = run_async(get_diagnosis_stats(hours))

    return jsonify({
        'anomalies': anomaly_stats,
        'diagnoses': diagnosis_stats
    })


@app.route('/api/stats/timeline')
def api_stats_timeline():
    """API endpoint for hourly anomaly timeline"""
    hours = int(request.args.get("hours", 168))
    timeline = run_async(get_anomaly_timeline(hours))
    return jsonify(timeline)


@app.route('/api/agents/register', methods=['POST'])
def api_register_agent():
    auth = require_admin()
    if auth:
        return auth
    payload = request.get_json(force=True)
    result = run_async(register_agent(payload))
    return jsonify(result)


async def register_agent(payload):
    conn = await get_db_connection()
    try:
        await conn.execute(
            """
            INSERT INTO agent_nodes
            (node_id, environment, agent_version, base_url, runner_capabilities, status, last_seen, metadata)
            VALUES ($1, $2, $3, $4, $5, 'registered', NOW(), $6)
            ON CONFLICT (node_id) DO UPDATE SET
                environment = EXCLUDED.environment,
                agent_version = EXCLUDED.agent_version,
                base_url = EXCLUDED.base_url,
                runner_capabilities = EXCLUDED.runner_capabilities,
                status = 'registered',
                last_seen = NOW(),
                metadata = EXCLUDED.metadata
            """,
            payload['node_id'],
            payload.get('environment', 'test'),
            payload.get('agent_version', 'unknown'),
            payload['base_url'],
            json.dumps(payload.get('runner_capabilities', {})),
            json.dumps(payload.get('metadata', {})),
        )
        await audit(conn, 'agent.registered', 'admin', None, None, payload['node_id'], 'allowed', None, 'success', payload)
        return {'status': 'registered', 'node_id': payload['node_id']}
    finally:
        await conn.close()


@app.route('/api/agents')
def api_agents():
    return jsonify(run_async(list_agents()))


async def list_agents():
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT node_id, environment, agent_version, base_url,
                   runner_capabilities, status, last_seen
            FROM agent_nodes
            ORDER BY node_id
            """
        )
        return [
            {
                'node_id': row['node_id'],
                'environment': row['environment'],
                'agent_version': row['agent_version'],
                'base_url': row['base_url'],
                'runner_capabilities': parse_jsonb(row['runner_capabilities']),
                'status': row['status'],
                'last_seen': row['last_seen'].isoformat(),
            }
            for row in rows
        ]
    finally:
        await conn.close()


@app.route('/api/agents/<node_id>/tasks', methods=['POST'])
def api_queue_agent_task(node_id):
    auth = require_admin()
    if auth:
        return auth
    key = request.headers.get('Idempotency-Key')
    if not key:
        return jsonify({'error': 'Idempotency-Key header is required'}), 400
    payload = request.get_json(silent=True) or {}
    result, status_code = run_async(queue_agent_task(node_id, key, payload))
    return jsonify(result), status_code


async def queue_agent_task(node_id, idempotency_key, payload):
    task_type = payload.get('task_type', 'rca')
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM idempotency_records WHERE expires_at <= NOW()")
            existing = await conn.fetchrow(
                """
                SELECT r.task_id, t.node_id, t.task_type, t.status, t.result
                FROM idempotency_records r
                JOIN agent_tasks t ON t.task_id = r.task_id
                WHERE r.idempotency_key = $1
                  AND r.scope = 'agent_task'
                  AND r.expires_at > NOW()
                """,
                idempotency_key,
            )
            if existing:
                if existing['node_id'] != node_id or existing['task_type'] != task_type:
                    return {'error': 'idempotency key already used for a different agent task'}, 400
                return {
                    'task_id': existing['task_id'],
                    'node_id': existing['node_id'],
                    'task_type': existing['task_type'],
                    'status': existing['status'],
                    'result': parse_jsonb(existing['result']),
                }, 200

            active = await conn.fetchrow(
                """
                SELECT r.task_id
                FROM idempotency_records r
                JOIN agent_tasks t ON t.task_id = r.task_id
                WHERE r.scope = 'agent_task'
                  AND t.node_id = $1
                  AND t.task_type = $2
                  AND r.expires_at > NOW()
                LIMIT 1
                """,
                node_id,
                task_type,
            )
            if active:
                return {'error': 'agent task already has an active idempotency key', 'task_id': active['task_id']}, 409

            node_exists = await conn.fetchval("SELECT TRUE FROM agent_nodes WHERE node_id = $1", node_id)
            if not node_exists:
                return {'error': 'agent not found'}, 404

            task_id = f"task_{uuid.uuid4().hex}"
            await conn.execute(
                """
                INSERT INTO agent_tasks
                (task_id, node_id, task_type, idempotency_key, status, schema_version, result)
                VALUES ($1, $2, $3, $4, 'queued', '1.0', '{}'::jsonb)
                """,
                task_id,
                node_id,
                task_type,
                idempotency_key,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records
                (idempotency_key, scope, task_id, expires_at, metadata)
                VALUES ($1, 'agent_task', $2, NOW() + $3 * INTERVAL '1 minute', $4)
                """,
                idempotency_key,
                task_id,
                IDEMPOTENCY_TTL_MINUTES,
                json.dumps({'node_id': node_id, 'task_type': task_type}),
            )
            await audit(conn, 'agent_task.queued', 'system-agent', None, None, node_id, 'allowed', idempotency_key, 'queued', {'task_id': task_id, 'task_type': task_type}, '1.0')
            return {'task_id': task_id, 'node_id': node_id, 'task_type': task_type, 'status': 'queued'}, 202
    finally:
        await conn.close()


@app.route('/api/plans/<plan_id>/approve', methods=['POST'])
def api_approve_plan(plan_id):
    auth = require_admin()
    if auth:
        return auth
    payload = request.get_json(silent=True) or {}
    result = run_async(approve_plan(plan_id, payload.get('reason')))
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify(result)


@app.route('/api/plans/<plan_id>/reject', methods=['POST'])
def api_reject_plan(plan_id):
    auth = require_admin()
    if auth:
        return auth
    payload = request.get_json(silent=True) or {}
    result = run_async(reject_plan(plan_id, payload.get('reason')))
    return jsonify(result)


async def approve_plan(plan_id, reason=None):
    conn = await get_db_connection()
    try:
        # Reject approval for non-executable plans (schema != 2.0).
        # A schema 1.0 fallback has no catalog steps — the Knowledge Agent would
        # block it anyway, but blocking here prevents the misleading 'approved' badge.
        plan_row = await conn.fetchrow(
            "SELECT schema_version, action_plan, plan_status FROM diagnosis_reports WHERE diagnosis_id = $1",
            plan_id,
        )
        if not plan_row:
            return {'error': 'plan not found'}, 404

        # Block approve on terminal statuses — these plans are already done or closed.
        TERMINAL_STATUSES = frozenset([
            'final_verified', 'kb_imported', 'kb_skipped', 'kb_import_failed',
            'rejected', 'blocked', 'execution_failed', 'execution_failed_unknown_state',
        ])
        if plan_row['plan_status'] in TERMINAL_STATUSES:
            return {'error': f'cannot approve — plan is already in terminal state: {plan_row["plan_status"]}'}, 409

        plan_dict = parse_jsonb(plan_row['action_plan']) or {}
        effective_schema = plan_dict.get('schema_version') or plan_row['schema_version'] or '1.0'
        if effective_schema not in SUPPORTED_EXECUTION_SCHEMAS:
            await audit(conn, 'policy.blocked', 'admin', plan_id, None, None,
                        'unsupported_schema', None, 'blocked', {}, effective_schema)
            return {'error': 'cannot approve — plan has no executable steps (unsupported schema)'}, 400

        # The approval records the canonical hash of the exact plan content the
        # human saw (including its execution_profile); the executor refuses to
        # run anything that hashes differently (ADR-006 drift signal 0).
        approved_hash = plan_sha256(plan_dict)
        approved_until = datetime.now(timezone.utc) + timedelta(minutes=APPROVAL_EXPIRY_MINUTES)
        await conn.execute(
            """
            INSERT INTO plan_approvals (plan_id, actor, decision, approved_until, reason, plan_sha256)
            VALUES ($1, 'admin', 'approved', $2, $3, $4)
            """,
            plan_id,
            approved_until,
            reason,
            approved_hash,
        )
        await conn.execute("UPDATE diagnosis_reports SET plan_status = 'approved' WHERE diagnosis_id = $1", plan_id)
        await audit(conn, 'plan.approved', 'admin', plan_id, None, None, 'allowed', None, 'approved', {'approved_until': approved_until.isoformat(), 'reason': reason, 'plan_sha256': approved_hash})
        return {'status': 'approved', 'plan_id': plan_id, 'approved_until': approved_until.isoformat(), 'plan_sha256': approved_hash}
    finally:
        await conn.close()


async def reject_plan(plan_id, reason=None):
    conn = await get_db_connection()
    try:
        await conn.execute(
            """
            INSERT INTO plan_approvals (plan_id, actor, decision, reason)
            VALUES ($1, 'admin', 'rejected', $2)
            """,
            plan_id,
            reason,
        )
        await conn.execute("UPDATE diagnosis_reports SET plan_status = 'rejected' WHERE diagnosis_id = $1", plan_id)
        await audit(conn, 'plan.rejected', 'admin', plan_id, None, None, 'blocked', None, 'rejected', {'reason': reason})
        return {'status': 'rejected', 'plan_id': plan_id}
    finally:
        await conn.close()


@app.route('/api/plans/<plan_id>/resume', methods=['POST'])
def api_resume_plan(plan_id):
    auth = require_admin()
    if auth:
        return auth
    result = run_async(resume_plan(plan_id))
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify(result)


@app.route('/api/plans/<plan_id>/abort', methods=['POST'])
def api_abort_plan(plan_id):
    auth = require_admin()
    if auth:
        return auth
    payload = request.get_json(silent=True) or {}
    result = run_async(abort_plan(plan_id, payload.get('reason')))
    if isinstance(result, tuple):
        return jsonify(result[0]), result[1]
    return jsonify(result)


@app.route('/api/plans/<plan_id>/escalations')
def api_plan_escalations(plan_id):
    return jsonify(run_async(get_escalations(plan_id=plan_id)))


@app.route('/api/escalations')
def api_escalations():
    status = request.args.get('status', 'open')
    return jsonify(run_async(get_escalations(status=status)))


async def get_escalations(plan_id=None, status=None):
    conn = await get_db_connection()
    try:
        clauses, params = [], []
        if plan_id:
            params.append(plan_id)
            clauses.append(f"plan_id = ${len(params)}")
        if status:
            params.append(status)
            clauses.append(f"status = ${len(params)}")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await conn.fetch(
            f"""
            SELECT escalation_id, execution_id, plan_id, node_id, drift_type, severity,
                   details, status, resolution, resolved_by, created_at, resolved_at
            FROM execution_escalations
            {where}
            ORDER BY created_at DESC
            LIMIT 100
            """,
            *params,
        )
        return [
            {
                **dict(row),
                'details': parse_jsonb(row['details']),
                'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                'resolved_at': row['resolved_at'].isoformat() if row['resolved_at'] else None,
            }
            for row in rows
        ]
    finally:
        await conn.close()


async def resume_plan(plan_id):
    """
    Re-queue a paused execution (ADR-006). Flow is "Re-approve & Resume": the
    pause invalidates trust in the old approval window, so a currently valid
    approval (fresh plan_sha256 binding) is required before anything re-runs.
    """
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            execution = await conn.fetchrow(
                """
                SELECT execution_id, status FROM plan_executions
                WHERE plan_id = $1
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                plan_id,
            )
            if not execution:
                return {'error': 'no execution found for plan'}, 404
            if execution['status'] != 'paused_for_review':
                return {'error': f"latest execution is '{execution['status']}', not paused_for_review"}, 409
            if not await approval_valid(conn, plan_id):
                return {'error': 'a currently valid approval is required — re-approve the plan first'}, 403

            await conn.execute(
                """
                UPDATE execution_escalations
                SET status = 'resolved', resolution = 'resumed', resolved_by = 'admin', resolved_at = NOW()
                WHERE execution_id = $1 AND status = 'open'
                """,
                execution['execution_id'],
            )
            await conn.execute(
                "UPDATE plan_executions SET status = 'queued', finished_at = NULL WHERE execution_id = $1",
                execution['execution_id'],
            )
            await conn.execute("UPDATE diagnosis_reports SET plan_status = 'queued' WHERE diagnosis_id = $1", plan_id)
            await audit(conn, 'execution.resumed', 'admin', plan_id, None, None, 'allowed', None, 'queued', {'execution_id': execution['execution_id']})
            return {'status': 'queued', 'plan_id': plan_id, 'execution_id': execution['execution_id']}
    finally:
        await conn.close()


async def abort_plan(plan_id, reason=None):
    """Terminate a paused execution; the open escalation resolves as aborted."""
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            execution = await conn.fetchrow(
                """
                SELECT execution_id, status FROM plan_executions
                WHERE plan_id = $1
                ORDER BY requested_at DESC
                LIMIT 1
                """,
                plan_id,
            )
            if not execution:
                return {'error': 'no execution found for plan'}, 404
            if execution['status'] != 'paused_for_review':
                return {'error': f"latest execution is '{execution['status']}', not paused_for_review"}, 409

            await conn.execute(
                """
                UPDATE execution_escalations
                SET status = 'resolved', resolution = 'aborted', resolved_by = 'admin', resolved_at = NOW()
                WHERE execution_id = $1 AND status = 'open'
                """,
                execution['execution_id'],
            )
            await conn.execute(
                """
                UPDATE plan_executions
                SET status = 'execution_failed', finished_at = NOW(),
                    result = $2
                WHERE execution_id = $1
                """,
                execution['execution_id'],
                json.dumps({'reason': 'aborted_by_human', 'detail': reason}),
            )
            await conn.execute("UPDATE diagnosis_reports SET plan_status = 'execution_failed' WHERE diagnosis_id = $1", plan_id)
            await audit(conn, 'execution.aborted', 'admin', plan_id, None, None, 'blocked', None, 'execution_failed', {'execution_id': execution['execution_id'], 'reason': reason})
            return {'status': 'execution_failed', 'plan_id': plan_id, 'execution_id': execution['execution_id']}
    finally:
        await conn.close()


@app.route('/api/plans/<plan_id>/execute', methods=['POST'])
def api_execute_plan(plan_id):
    auth = require_admin()
    if auth:
        return auth
    key = request.headers.get('Idempotency-Key')
    if not key:
        return jsonify({'error': 'Idempotency-Key header is required'}), 400
    result, status_code = run_async(queue_execution(plan_id, key))
    return jsonify(result), status_code


async def queue_execution(plan_id, idempotency_key):
    conn = await get_db_connection()
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM idempotency_records WHERE expires_at <= NOW()")
            existing_record = await conn.fetchrow(
                """
                SELECT r.plan_id, r.execution_id, e.status, e.result
                FROM idempotency_records r
                JOIN plan_executions e ON e.execution_id = r.execution_id
                WHERE r.idempotency_key = $1
                  AND r.scope = 'plan_execute'
                  AND r.expires_at > NOW()
                """,
                idempotency_key,
            )
            if existing_record:
                if existing_record['plan_id'] != plan_id:
                    return {'error': 'idempotency key already used for a different plan'}, 400
                return {
                    'execution_id': existing_record['execution_id'],
                    'plan_id': existing_record['plan_id'],
                    'status': existing_record['status'],
                    'result': parse_jsonb(existing_record['result']),
                }, 200

            active_plan_record = await conn.fetchrow(
                """
                SELECT idempotency_key, execution_id
                FROM idempotency_records
                WHERE scope = 'plan_execute'
                  AND plan_id = $1
                  AND expires_at > NOW()
                LIMIT 1
                """,
                plan_id,
            )
            if active_plan_record:
                return {
                    'error': 'plan already has an active execution idempotency key',
                    'execution_id': active_plan_record['execution_id'],
                }, 409

            diagnosis = await conn.fetchrow(
                "SELECT action_plan, schema_version FROM diagnosis_reports WHERE diagnosis_id = $1",
                plan_id,
            )
            if not diagnosis:
                return {'error': 'plan not found'}, 404

            plan = parse_jsonb(diagnosis['action_plan']) or {}
            effective_schema = plan.get('schema_version', diagnosis['schema_version'])
            if effective_schema not in SUPPORTED_EXECUTION_SCHEMAS:
                await audit(conn, 'policy.blocked', 'admin', plan_id, None, None, 'unsupported_schema', idempotency_key, 'blocked', {}, effective_schema)
                return {'error': 'unsupported schema'}, 400

            target_node_id = first_target_node(plan)
            if not await approval_valid(conn, plan_id) and not plan_auto_allowed(plan):
                await audit(conn, 'policy.blocked', 'admin', plan_id, None, target_node_id, 'approval_required', idempotency_key, 'blocked', {}, SUPPORTED_EXECUTION_SCHEMA)
                return {'error': 'approval required or expired'}, 403

            execution_id = f"exec_{uuid.uuid4().hex}"
            await conn.execute(
                """
                INSERT INTO plan_executions
                (execution_id, plan_id, idempotency_key, status, target_node_id, schema_version)
                VALUES ($1, $2, $3, 'queued', $4, $5)
                """,
                execution_id,
                plan_id,
                idempotency_key,
                target_node_id,
                effective_schema,
            )
            await conn.execute(
                """
                INSERT INTO idempotency_records
                (idempotency_key, scope, plan_id, execution_id, expires_at, metadata)
                VALUES ($1, 'plan_execute', $2, $3,
                        NOW() + $4 * INTERVAL '1 minute', $5)
                """,
                idempotency_key,
                plan_id,
                execution_id,
                IDEMPOTENCY_TTL_MINUTES,
                json.dumps({'requested_by': 'admin'}),
            )
            await conn.execute("UPDATE diagnosis_reports SET plan_status = 'queued' WHERE diagnosis_id = $1", plan_id)
            await audit(conn, 'execution.queued', 'admin', plan_id, None, target_node_id, 'allowed', idempotency_key, 'queued', {'execution_id': execution_id}, SUPPORTED_EXECUTION_SCHEMA)
            return {'execution_id': execution_id, 'plan_id': plan_id, 'status': 'queued'}, 202
    finally:
        await conn.close()


@app.route('/api/plans/<plan_id>/execution')
def api_plan_execution(plan_id):
    return jsonify(run_async(get_plan_execution(plan_id)))


async def get_plan_execution(plan_id):
    conn = await get_db_connection()
    try:
        executions = await conn.fetch(
            """
            SELECT execution_id, plan_id, idempotency_key, status, target_node_id,
                   requested_at, started_at, finished_at, retry_count, result
            FROM plan_executions
            WHERE plan_id = $1
            ORDER BY requested_at DESC
            """,
            plan_id,
        )
        steps = await conn.fetch(
            """
            SELECT execution_id, step_id, status, command_id, target_node_id,
                   retry_count, started_at, finished_at, result
            FROM execution_steps
            WHERE execution_id = ANY($1::text[])
            ORDER BY execution_id, step_id
            """,
            [row['execution_id'] for row in executions],
        )
        by_execution = {}
        for row in steps:
            by_execution.setdefault(row['execution_id'], []).append({
                'step_id': row['step_id'],
                'status': row['status'],
                'command_id': row['command_id'],
                'target_node_id': row['target_node_id'],
                'retry_count': row['retry_count'],
                'started_at': row['started_at'].isoformat() if row['started_at'] else None,
                'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None,
                'result': parse_jsonb(row['result']),
            })
        return [
            {
                'execution_id': row['execution_id'],
                'plan_id': row['plan_id'],
                'idempotency_key': row['idempotency_key'],
                'status': row['status'],
                'target_node_id': row['target_node_id'],
                'requested_at': row['requested_at'].isoformat(),
                'started_at': row['started_at'].isoformat() if row['started_at'] else None,
                'finished_at': row['finished_at'].isoformat() if row['finished_at'] else None,
                'retry_count': row['retry_count'],
                'result': parse_jsonb(row['result']),
                'steps': by_execution.get(row['execution_id'], []),
            }
            for row in executions
        ]
    finally:
        await conn.close()


@app.route('/api/plans/<plan_id>/approval')
def api_plan_approval(plan_id):
    """Latest approval decision for a plan (read-only; powers the 30-min countdown)."""
    return jsonify(run_async(get_latest_approval(plan_id)))


async def get_latest_approval(plan_id):
    conn = await get_db_connection()
    try:
        row = await conn.fetchrow(
            """
            SELECT decision, approved_until, reason, actor, created_at
            FROM plan_approvals
            WHERE plan_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            plan_id,
        )
        if not row:
            return {}
        return {
            'decision': row['decision'],
            'approved_until': row['approved_until'].isoformat() if row['approved_until'] else None,
            'reason': row['reason'],
            'actor': row['actor'],
            'created_at': row['created_at'].isoformat() if row['created_at'] else None,
        }
    finally:
        await conn.close()


async def approval_valid(conn, plan_id):
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
    if not row or row['decision'] != 'approved' or not row['approved_until']:
        return False
    approved_until = row['approved_until']
    if approved_until.tzinfo is None:
        approved_until = approved_until.replace(tzinfo=timezone.utc)
    return approved_until > datetime.now(timezone.utc)


def plan_auto_allowed(plan):
    if plan.get('schema_version') in SUPPORTED_EXECUTION_SCHEMAS:
        policy = plan.get('environment_policy') or {}
        return (
            plan.get('risk_level') == 'low'
            and bool(plan.get('steps'))
            and policy.get('environment') == 'test'
            and policy.get('auto_execute_allowed') is True
            and not policy.get('security_review_required')
        )
    for step in plan.get('execution_steps', []):
        if step.get('phase') != 'Execute':
            continue
        for command in step.get('commands', []):
            policy = command.get('environment_policy') or {}
            if not (policy.get('environment') == 'test' and policy.get('auto_execute_allowed') is True):
                return False
    return True


def first_target_node(plan):
    if plan.get('schema_version') in SUPPORTED_EXECUTION_SCHEMAS:
        return plan.get('target_node_id')
    for step in plan.get('execution_steps', []):
        for command in step.get('commands', []):
            if command.get('target_node_id'):
                return command['target_node_id']
    return None


async def audit(conn, event_type, actor, plan_id, step_id, node_id, policy_decision, idempotency_key, result, metadata, schema_version=SUPPORTED_EXECUTION_SCHEMA):
    await conn.execute(
        """
        INSERT INTO audit_events
        (actor, event_type, plan_id, step_id, node_id, schema_version,
         policy_decision, idempotency_key, result, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        actor,
        event_type,
        plan_id,
        step_id,
        node_id,
        schema_version,
        policy_decision,
        idempotency_key,
        result,
        json.dumps(metadata),
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
