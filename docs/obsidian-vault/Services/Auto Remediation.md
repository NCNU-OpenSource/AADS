---
title: Auto Remediation
type: component
layer: 3
tags: [service, layer3, remediation, automation]
created: 2026-04-08
---

# Auto Remediation

## Overview

Auto Remediation 是自動修復功能模組，**預設關閉**，需要明確啟用才會執行自動化操作。

## Role in System

- 執行低風險的自動修復操作
- 僅執行標記為 `safe_to_automate` 的建議
- 需要明確配置啟用

## Safety Design

### 預設關閉

```yaml
# .env
AUTO_REMEDIATION_ENABLED=false  # 預設值
```

### 安全操作定義

只有以下類型的操作被標記為 `safe_to_automate`:

| 操作類型 | 風險等級 | 安全自動化 |
|----------|----------|------------|
| 健康檢查 | Low | ✅ Yes |
| 日誌收集 | Low | ✅ Yes |
| 服務重啟 | Low-Medium | ✅ Yes (with limits) |
| 配置變更 | Medium | ❌ No |
| 資源調整 | Medium | ❌ No |
| 數據修改 | High | ❌ No |

## Source Code

**位置:** `layer3-remediation/src/`

```python
import os
import subprocess
from typing import Optional

class AutoRemediation:
    def __init__(self):
        self.enabled = os.environ.get("AUTO_REMEDIATION_ENABLED", "false").lower() == "true"
        self.max_restarts_per_hour = int(os.environ.get("MAX_RESTARTS_PER_HOUR", "3"))
        self.restart_counts = {}  # container -> count
    
    async def execute(self, suggestion: dict) -> dict:
        """Execute a remediation action"""
        if not self.enabled:
            return {
                "executed": False,
                "reason": "auto_remediation_disabled"
            }
        
        if not suggestion.get("safe_to_automate", False):
            return {
                "executed": False,
                "reason": "not_safe_to_automate"
            }
        
        if suggestion.get("risk_level") not in ["low"]:
            return {
                "executed": False,
                "reason": "risk_level_too_high"
            }
        
        command = suggestion.get("command")
        if not command:
            return {
                "executed": False,
                "reason": "no_command"
            }
        
        # Rate limiting for restarts
        if "restart" in command:
            container = self._extract_container(command)
            if not self._can_restart(container):
                return {
                    "executed": False,
                    "reason": "restart_rate_limit"
                }
        
        # Execute command
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=60
            )
            
            return {
                "executed": True,
                "command": command,
                "return_code": result.returncode,
                "stdout": result.stdout.decode(),
                "stderr": result.stderr.decode()
            }
        except subprocess.TimeoutExpired:
            return {
                "executed": False,
                "reason": "timeout"
            }
        except Exception as e:
            return {
                "executed": False,
                "reason": str(e)
            }
    
    def _can_restart(self, container: str) -> bool:
        """Check restart rate limit"""
        current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
        key = f"{container}:{current_hour}"
        
        count = self.restart_counts.get(key, 0)
        if count >= self.max_restarts_per_hour:
            return False
        
        self.restart_counts[key] = count + 1
        return True
```

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AUTO_REMEDIATION_ENABLED` | `false` | 是否啟用自動修復 |
| `MAX_RESTARTS_PER_HOUR` | `3` | 每小時最大重啟次數 |
| `ALLOWED_OPERATIONS` | `restart,health_check` | 允許的操作類型 |

## Safeguards

### 1. 預設關閉
必須明確設置 `AUTO_REMEDIATION_ENABLED=true`

### 2. 風險等級檢查
只執行 `risk_level: low` 的操作

### 3. 安全標記檢查
只執行 `safe_to_automate: true` 的建議

### 4. 速率限制
每個容器每小時最多重啟 3 次

### 5. 超時控制
命令執行超時 60 秒自動終止

### 6. 審計日誌
所有操作都記錄到資料庫

## Audit Log

```sql
CREATE TABLE remediation_logs (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    diagnosis_id TEXT,
    command TEXT,
    executed BOOLEAN,
    reason TEXT,
    result JSONB
);
```

## Best Practices

1. **先測試再啟用:** 在 staging 環境驗證後再啟用
2. **監控操作:** 定期檢查 remediation_logs
3. **限制範圍:** 使用 ALLOWED_OPERATIONS 限制操作類型
4. **人工審核:** 高風險操作永遠需要人工確認

## Related

- [[Suggestion Generator]] - 生成建議
- [[Notification Hub]] - 通知人工審核
- [[Layer 3 - Remediation]]
