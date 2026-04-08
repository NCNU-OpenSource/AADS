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

### [[Layer 2 - Root Cause Analysis|Layer 2: Root Cause Analysis]]
> 根因分析層

- [[Anomaly Consumer]] - 異常消費者
- [[Anomaly Aggregator]] - 異常聚類
- [[Metrics Correlator]] - 指標關聯
- [[Knowledge Base]] - 知識庫 (ChromaDB)
- [[LLM Reasoner]] - LLM 推理引擎
- [[Suggestion Generator]] - 建議生成
- [[Notification Hub]] - 通知中樞

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
