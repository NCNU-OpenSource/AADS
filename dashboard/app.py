"""
Simple Dashboard for AI Auto-Debug System
Display anomalies and diagnosis reports
"""
from flask import Flask, render_template, jsonify
import asyncpg
import asyncio
import os
import json
from datetime import datetime, timedelta

app = Flask(__name__)

# Database configuration
DB_HOST = os.getenv('DB_HOST', 'timescaledb')
DB_PORT = int(os.getenv('DB_PORT', '5432'))
DB_NAME = os.getenv('DB_NAME', 'logdb')
DB_USER = os.getenv('DB_USER', 'logdb')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'logdb_password')


async def get_db_connection():
    """Create database connection"""
    return await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )


async def get_diagnosis_reports(hours=24):
    """Get recent diagnosis reports"""
    conn = await get_db_connection()
    try:
        rows = await conn.fetch(
            """
            SELECT diagnosis_id, timestamp, severity, summary,
                   root_cause, recommended_actions
            FROM diagnosis_reports
            WHERE timestamp > NOW() - INTERVAL '%s hours'
            ORDER BY timestamp DESC
            LIMIT 50
            """ % hours
        )

        reports = []
        for row in rows:
            reports.append({
                'diagnosis_id': row['diagnosis_id'],
                'timestamp': row['timestamp'].isoformat(),
                'severity': row['severity'],
                'summary': row['summary'],
                'root_cause': json.loads(row['root_cause']) if isinstance(row['root_cause'], str) else row['root_cause'],
                'recommended_actions': json.loads(row['recommended_actions']) if isinstance(row['recommended_actions'], str) else row['recommended_actions']
            })

        return reports
    finally:
        await conn.close()


async def get_anomaly_stats(hours=24):
    """Get anomaly statistics"""
    conn = await get_db_connection()
    try:
        # Total anomalies
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM anomaly_logs WHERE time > NOW() - INTERVAL '%s hours'" % hours
        )

        # By container
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

        # By filter stage
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


async def get_diagnosis_stats(hours=24):
    """Get diagnosis statistics"""
    conn = await get_db_connection()
    try:
        # By severity
        by_severity = await conn.fetch(
            """
            SELECT severity, COUNT(*) as count
            FROM diagnosis_reports
            WHERE timestamp > NOW() - INTERVAL '%s hours'
            GROUP BY severity
            ORDER BY
                CASE severity
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
            """ % hours
        )

        # Total
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM diagnosis_reports WHERE timestamp > NOW() - INTERVAL '%s hours'" % hours
        )

        return {
            'total': total,
            'by_severity': [dict(row) for row in by_severity]
        }
    finally:
        await conn.close()


@app.route('/')
def index():
    """Main dashboard page"""
    return render_template('index.html')


@app.route('/api/diagnosis')
def api_diagnosis():
    """API endpoint for diagnosis reports"""
    hours = int(request.args.get('hours', 24))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    reports = loop.run_until_complete(get_diagnosis_reports(hours))
    loop.close()
    return jsonify(reports)


@app.route('/api/stats')
def api_stats():
    """API endpoint for statistics"""
    hours = int(request.args.get('hours', 24))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    anomaly_stats = loop.run_until_complete(get_anomaly_stats(hours))
    diagnosis_stats = loop.run_until_complete(get_diagnosis_stats(hours))

    loop.close()

    return jsonify({
        'anomalies': anomaly_stats,
        'diagnoses': diagnosis_stats
    })


if __name__ == '__main__':
    from flask import request
    app.run(host='0.0.0.0', port=5000, debug=True)
