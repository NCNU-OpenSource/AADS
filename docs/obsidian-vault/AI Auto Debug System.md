---
title: AI Auto Debug System
type: MOC
tags: [moc, architecture, overview]
created: 2026-04-08
---

# AI Auto Debug System

四層 AI 驅動的自動除錯系統，靈感來自 Metoro.io。

## System Architecture

![[Architecture-Overview.canvas]]

**完整流程圖**: [[System Flow]]

## Layer Overview

### [[Layer 0 - Data Collection|Layer 0: Data Collection & Storage]]
> 數據收集與永久存儲層

- [[Grafana Alloy]] - 日誌收集器
- [[Loki]] - 日誌聚合系統
- [[TimescaleDB]] - 時序資料庫
- [[Prometheus]] - 指標監控
- [[cAdvisor]] - 容器監控
- [[DCGM Exporter]] - GPU 監控

### [[Layer 1 - Anomaly Filtering|Layer 1: Anomaly Filtering]]
> 多層異常過濾層

- [[Layer 1 Filter]] - 異常過濾服務
- [[LogBERT]] - BERT 異常檢測引擎
- [[Services/FastAPI Ingester|FastAPI Ingester]] - 異常資料接收器 (Port 8000)

### [[Layer 2 - Root Cause Analysis|Layer 2: Root Cause Analysis]]
> 根因分析層

- [[Anomaly Consumer]] - 異常消費者
- [[Anomaly Aggregator]] - 異常聚類
- [[Metrics Correlator]] - 指標關聯
- [[Knowledge Base]] - 知識庫 (ChromaDB)
- [[LLM Reasoner]] - LLM 推理引擎
- [[Suggestion Generator]] - 建議生成
- [[Notification Hub]] - 通知中樞
- [[Services/Layer 2 Webhook|Layer 2 Webhook]] - 即時分析觸發器 (Port 8080)

### [[Layer 3 - Remediation|Layer 3: Remediation & Notification]]
> 修復建議與通知層

- [[Auto Remediation]] - 自動修復（預設關閉）

## Frontend & Visualization

- [[Dashboard]] - Flask Web UI
- [[Grafana Dashboard]] - 可視化儀表板

## External Services

- [[OpenAI API]] - GPT 模型 API
- [[Ollama]] - 本地 LLM 部署

## Database

- [[Database Schema]] - 資料庫結構

---

## Architecture Decision Records (ADR)

技術決策記錄，說明每個技術選擇的原因和研究過程。

- [[ADR/ADR-001-Metrics-Collection-Frequency|ADR-001: 採集頻率選擇]] - 為何選擇每分鐘採集
- [[ADR/ADR-002-Event-Driven-Log-Collection|ADR-002: 事件驅動日誌收集]] - Layer 0-2 完整架構實作
- [[ADR/ADR-003-System-Integration-Fixes|ADR-003: 系統整合修復]] - 從檔案到部署的完整修復

## Research

研究報告，記錄調查過程和發現。

- [[Research/Cloud Provider Metrics Collection|雲端廠商採集頻率研究]] - AWS/Azure/GCP 的預設設定

## Debug Log

問題修復記錄，記錄遇到的問題、解決方案和學習心得。

- [[Debug-Log/2026-04-08-System-Integration-Debug|2026-04-08: 系統整合問題修復]] - Alloy 語法、Fan-out 架構、Migrations

---

## Tech Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.11 |
| Web Framework | Flask |
| Log Collection | Grafana Alloy, Loki |
| Metrics | Prometheus, cAdvisor |
| Time-series DB | TimescaleDB |
| Vector DB | ChromaDB |
| AI/ML | BERT, PyTorch, Transformers |
| LLM | OpenAI API, Ollama |
| Visualization | Grafana |
| Container | Docker Compose |

## Quick Links

- [[Docker Compose Services]]
- [[API Endpoints]]
- [[Configuration Files]]
- [[Deployment-Verification]] - 部署前驗證指南

---

## 🎯 Key Features

### Event-Driven Architecture (零延遲)

```
LogBERT 異常檢測
    ↓
POST to Alloy :9999
    ↓
Fan-out 同時轉發:
  ├─► Ingester :8000 (PostgreSQL 寫入)
  └─► Layer 2 Webhook :8080 (LLM 即時分析)
```

### 自動化資料管理

- **本機日誌**: 7 天保留（logrotate + auditd）
- **TimescaleDB**: 7 天自動壓縮，90 天自動刪除
- **Metrics 採集**: 60 秒頻率（對齊 AWS/GCP）

### 完整監控

- **Logs**: /var/log/* + audit.log（事件驅動 tail）
- **Metrics**: CPU/Memory + Process List（/host/proc）
- **Commands**: auditd 記錄所有命令執行
