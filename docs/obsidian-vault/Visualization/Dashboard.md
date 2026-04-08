---
title: Dashboard
type: service
layer: frontend
tags: [frontend, flask, web-ui]
port: 5000
created: 2026-04-08
---

# Dashboard

## Overview

Dashboard 是基於 Flask 的 Web UI，提供診斷報告查看和系統統計功能。

## Role in System

- 顯示診斷報告列表
- 提供異常統計視覺化
- 查詢歷史診斷

## Source Code

**位置:** `dashboard/`

### Flask Application

`app.py`:
```python
from flask import Flask, render_template, jsonify, request
import asyncpg
from datetime import datetime, timedelta

app = Flask(__name__)

@app.route("/")
def index():
    """Main dashboard page"""
    return render_template("index.html")

@app.route("/api/diagnosis", methods=["GET"])
async def get_diagnosis():
    """Get diagnosis reports"""
    hours = int(request.args.get("hours", 24))
    since = datetime.utcnow() - timedelta(hours=hours)
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('''
            SELECT id, created_at, severity, root_cause, suggestions
            FROM diagnosis_reports
            WHERE created_at > $1
            ORDER BY created_at DESC
            LIMIT 100
        ''', since)
        
        return jsonify([{
            "id": row["id"],
            "created_at": row["created_at"].isoformat(),
            "severity": row["severity"],
            "root_cause": row["root_cause"],
            "suggestions": row["suggestions"]
        } for row in rows])
    finally:
        await conn.close()

@app.route("/api/stats", methods=["GET"])
async def get_stats():
    """Get anomaly and diagnosis statistics"""
    hours = int(request.args.get("hours", 24))
    since = datetime.utcnow() - timedelta(hours=hours)
    
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Anomaly counts by severity
        severity_stats = await conn.fetch('''
            SELECT severity, COUNT(*) as count
            FROM diagnosis_reports
            WHERE created_at > $1
            GROUP BY severity
        ''', since)
        
        # Anomaly counts by hour
        hourly_stats = await conn.fetch('''
            SELECT 
                date_trunc('hour', timestamp) as hour,
                COUNT(*) as count
            FROM anomaly_logs
            WHERE timestamp > $1
            GROUP BY hour
            ORDER BY hour
        ''', since)
        
        return jsonify({
            "severity": {row["severity"]: row["count"] for row in severity_stats},
            "hourly": [{
                "hour": row["hour"].isoformat(),
                "count": row["count"]
            } for row in hourly_stats]
        })
    finally:
        await conn.close()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

### Frontend Template

`templates/index.html`:
```html
<!DOCTYPE html>
<html>
<head>
    <title>AI Auto Debug System</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
    <h1>AI Auto Debug System</h1>
    
    <div id="stats">
        <canvas id="severityChart"></canvas>
        <canvas id="hourlyChart"></canvas>
    </div>
    
    <div id="diagnosis-list">
        <h2>Recent Diagnoses</h2>
        <table id="diagnosis-table">
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Severity</th>
                    <th>Root Cause</th>
                </tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>
    
    <script>
        // Fetch and render data
        async function loadData() {
            const stats = await fetch('/api/stats?hours=24').then(r => r.json());
            const diagnosis = await fetch('/api/diagnosis?hours=24').then(r => r.json());
            
            renderCharts(stats);
            renderTable(diagnosis);
        }
        
        loadData();
    </script>
</body>
</html>
```

## API Endpoints

| Endpoint | Method | Parameters | Description |
|----------|--------|------------|-------------|
| `/` | GET | - | 主頁面 |
| `/api/diagnosis` | GET | `hours` (default: 24) | 診斷報告列表 |
| `/api/stats` | GET | `hours` (default: 24) | 統計數據 |

## Docker Compose

```yaml
dashboard:
  build:
    context: ./dashboard
  ports:
    - "5000:5000"
  environment:
    DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@timescaledb:5432/autodebug
  depends_on:
    - timescaledb
```

## Features

- **診斷列表:** 顯示最近的診斷報告
- **嚴重程度分布:** 圓餅圖顯示各嚴重程度的比例
- **時間趨勢:** 折線圖顯示異常發生趨勢
- **詳情查看:** 點擊查看完整診斷和建議

## Data Flow

```mermaid
graph LR
    A[Browser] --> B[Dashboard Flask]
    B --> C[TimescaleDB]
    C -->|diagnosis_reports| B
    C -->|anomaly_logs| B
```

## Related

- [[TimescaleDB]] - 資料來源
- [[Grafana Dashboard]] - 更詳細的監控
- [[Layer 2 - Root Cause Analysis]] - 診斷來源
