# LogBERT 日誌異常檢測整合指南

## 概述

LogBERT 是基於 BERT 的 AI 日誌異常檢測系統，能夠自動識別異常日誌模式。

### 核心功能

- **Runtime 檢測**：每 5 分鐘自動從 Loki 查詢新日誌
- **AI 分析**：使用 BERT Masked Language Model 計算異常分數
- **自動儲存**：異常日誌自動儲存到 JSON 檔案

---

## 架構說明

```
Loki (日誌來源)
    ↓
LogBERT Python 服務
    ├─ Loki Client: 查詢日誌
    ├─ Log Processor: 使用 Drain3 提取模板
    ├─ Anomaly Detector: BERT 推論引擎
    └─ Output: 儲存異常到 JSON
```

---

## 部署資訊

### 服務配置

| 項目 | 值 |
|------|-----|
| **Container 名稱** | `logbert` |
| **映像** | 自建（基於 pytorch/pytorch:2.4.0-cuda11.8-cudnn9-runtime） |
| **運算模式** | CPU（GPU RTX 5060 Ti 不相容 PyTorch 2.4） |
| **檢查間隔** | 300 秒（5 分鐘） |
| **異常閾值** | 0.5 |
| **輸出目錄** | `~/Developer/Grafana/logbert/output/` |

### 目錄結構

```
~/Developer/Grafana/logbert/
├── src/
│   ├── loki_client.py        # Loki HTTP API 客戶端
│   ├── log_processor.py      # 日誌預處理（Drain3）
│   ├── anomaly_detector.py   # BERT 異常檢測
│   └── main.py               # 主程式（定時任務）
├── output/
│   ├── anomalies.json        # 累積的異常日誌（最多 10000 條）
│   └── anomalies_*.json      # 每次檢測的異常（時間戳）
├── models/                   # 訓練好的模型（可選）
└── requirements.txt          # Python 依賴
```

---

## 使用方式

### 啟動服務

```bash
cd ~/Developer/Grafana
docker compose up -d logbert
```

### 查看即時日誌

```bash
docker logs -f logbert
```

### 檢查異常輸出

```bash
# 查看所有異常
cat ~/Developer/Grafana/logbert/output/anomalies.json | jq '.'

# 只看最近 10 條
cat ~/Developer/Grafana/logbert/output/anomalies.json | jq '.[-10:]'

# 篩選高分數異常（>0.7）
cat ~/Developer/Grafana/logbert/output/anomalies.json | jq '.[] | select(.anomaly_score > 0.7)'
```

---

## 調整配置

### 修改檢查間隔

編輯 `docker-compose.yaml`：

```yaml
logbert:
  environment:
    - CHECK_INTERVAL=60  # 改為 1 分鐘
```

然後重啟：

```bash
docker compose up -d logbert
```

### 調整異常閾值

```yaml
logbert:
  environment:
    - ANOMALY_THRESHOLD=0.3  # 更敏感（更多異常）
    # 或
    - ANOMALY_THRESHOLD=0.7  # 更保守（只抓明顯異常）
```

---

## 測試異常檢測

### 自動化測試腳本

```bash
cd ~/Developer/Grafana
./test_anomaly_detection.sh
```

### 測試場景

| 場景 | 操作 | 預期異常 |
|------|------|----------|
| Container 停止 | `docker stop immich_machine_learning` | 停止日誌 |
| 安裝錯誤 | 安裝不存在的套件 | Package not found |
| 權限錯誤 | 存取無權限檔案 | Permission denied |
| 網路錯誤 | 連接不存在的主機 | Connection refused |

### 手動產生異常

```bash
# 方法 1: Kill 容器
docker stop immich_machine_learning
sleep 10
docker start immich_machine_learning

# 方法 2: 產生錯誤日誌
docker exec grafana apt-get install nonexistent-package || true

# 方法 3: 權限錯誤
docker exec grafana cat /root/.bashrc || true
```

---

## 異常檢測原理

### 工作流程

1. **日誌收集**：從 Loki 查詢最近 5 分鐘的 Docker 日誌
2. **正規化**：使用正則表達式替換時間戳、IP、UUID 等變數
3. **模板提取**：使用 Drain3 演算法提取日誌模板
4. **異常評分**：
   - 將日誌模板組成序列
   - BERT 嘗試預測被遮蔽的 tokens
   - 預測錯誤越多 = 異常分數越高
5. **閾值判斷**：分數 > 0.5 → 標記為異常

### 異常分數解讀

| 分數範圍 | 意義 |
|---------|------|
| 0.0 - 0.3 | 正常日誌 |
| 0.3 - 0.5 | 可疑日誌 |
| 0.5 - 0.7 | 異常日誌（預設閾值） |
| 0.7 - 1.0 | 高度異常 |

---

## 進階功能

### Fine-tuning 模型

如果想針對你的日誌訓練專屬模型：

```bash
# 1. 匯出 Loki 日誌作為訓練資料
curl -G "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={source="docker"}' \
  --data-urlencode 'limit=50000' \
  --data-urlencode "start=$(date -d '7 days ago' +%s)000000000" \
  --data-urlencode "end=$(date +%s)000000000" \
  > training_logs.json

# 2. 使用 LogBERT 官方工具訓練
# https://github.com/HelenGuohx/logbert

# 3. 將訓練好的模型放到 ~/Developer/Grafana/logbert/models/
# 4. 修改 anomaly_detector.py 的 model_path
```

### 整合告警

可以與 Grafana Alerting 整合：

```python
# 在 main.py 中新增
def send_alert(anomalies: List[Dict]):
    """Send alert to Grafana/Slack/Email"""
    if len(anomalies) > 10:  # 如果異常超過 10 條
        # 發送告警
        pass
```

---

## 故障排除

### 問題 1: LogBERT 無法啟動

```bash
# 查看錯誤訊息
docker logs logbert

# 常見原因：
# - Loki 未啟動：docker compose up -d loki
# - 映像未建置：docker compose build logbert
```

### 問題 2: 沒有檢測到異常

```bash
# 可能原因：
# 1. 閾值太高 → 降低 ANOMALY_THRESHOLD
# 2. 日誌太少 → 產生更多測試日誌
# 3. 還在初始化 → 等待 5 分鐘

# 檢查 Loki 日誌數量
curl -G 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={source="docker"}' \
  --data-urlencode 'limit=1' | jq '.data.stats.summary.totalLinesProcessed'
```

### 問題 3: CPU 使用率過高

```bash
# LogBERT 使用 CPU 模式（GPU 不相容）
# 解決方案：
# 1. 增加檢查間隔：CHECK_INTERVAL=600（10 分鐘）
# 2. 減少日誌量：限制 Loki 查詢範圍
# 3. 使用更小的 BERT 模型
```

---

## 效能調優

### CPU 模式優化

```yaml
logbert:
  deploy:
    resources:
      limits:
        cpus: '2.0'       # 限制 CPU 使用
        memory: '4G'      # 限制記憶體
```

### 減少資料量

```python
# 在 main.py 中
logs = loki.query_logs(
    query='{source="docker", container!~"alloy|loki|prometheus"}',  # 排除監控容器
    limit=1000  # 減少查詢數量
)
```

---

## 參考資源

- [LogBERT 論文](https://arxiv.org/abs/2103.04475)
- [LogBERT GitHub](https://github.com/HelenGuohx/logbert)
- [Drain3 文檔](https://github.com/logpai/Drain3)
- [Loki HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/)
- [BERT 模型 (Hugging Face)](https://huggingface.co/bert-base-uncased)

---

## 版本資訊

- **LogBERT Version**: Custom implementation based on LogBERT paper
- **BERT Model**: bert-base-uncased
- **PyTorch**: 2.4.0
- **Python**: 3.11
- **Drain3**: 0.9.11
- **Transformers**: 5.3.0

---

最後更新：2026-03-26
