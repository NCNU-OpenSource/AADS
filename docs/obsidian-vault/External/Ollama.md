---
title: Ollama
type: external
layer: external
tags: [external, llm, local, ollama]
port: 11434
created: 2026-04-08
---

# Ollama

## Overview

Ollama 是本地 LLM 部署平台，支援運行各種開源大型語言模型，無需雲端 API。

## Role in System

- 為 [[LLM Reasoner]] 提供本地推理能力
- 快速分類和初步分析
- 離線或成本敏感場景

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_URL` | `http://ollama:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `llama2` | 預設模型 |

### Recommended Models

| Model | Size | Best For |
|-------|------|----------|
| `llama2` | 7B | 通用分析，平衡速度與品質 |
| `llama2:13b` | 13B | 更好的推理能力 |
| `mistral` | 7B | 快速回應，良好的指令遵循 |
| `codellama` | 7B | 程式碼相關分析 |
| `mixtral` | 8x7B | 最佳本地推理（需高記憶體） |

## Docker Compose

```yaml
ollama:
  image: ollama/ollama:latest
  ports:
    - "11434:11434"
  volumes:
    - ollama-data:/root/.ollama
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

## Client Implementation

**位置:** `layer2-analyzer/src/llm/ollama.py`

```python
import aiohttp
import json
from typing import Optional

class OllamaClient:
    def __init__(
        self,
        base_url: str = "http://ollama:11434",
        model: str = "llama2"
    ):
        self.base_url = base_url
        self.model = model
    
    async def complete(self, prompt: str) -> dict:
        """Generate completion"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json"
                }
            ) as resp:
                data = await resp.json()
                return json.loads(data["response"])
    
    async def chat(self, messages: list[dict]) -> str:
        """Chat completion"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False
                }
            ) as resp:
                data = await resp.json()
                return data["message"]["content"]
    
    async def pull_model(self, model_name: str) -> bool:
        """Pull a model"""
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/pull",
                json={"name": model_name}
            ) as resp:
                return resp.status == 200
    
    async def list_models(self) -> list[str]:
        """List available models"""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/api/tags") as resp:
                data = await resp.json()
                return [m["name"] for m in data.get("models", [])]
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/generate` | POST | 生成完成 |
| `/api/chat` | POST | 對話完成 |
| `/api/pull` | POST | 拉取模型 |
| `/api/tags` | GET | 列出模型 |
| `/api/show` | POST | 模型資訊 |

## Model Management

### Pull Model

```bash
# Via CLI
docker exec ollama ollama pull llama2

# Via API
curl http://localhost:11434/api/pull -d '{"name": "llama2"}'
```

### List Models

```bash
docker exec ollama ollama list
```

## Performance

### Hardware Requirements

| Model Size | RAM | VRAM |
|------------|-----|------|
| 7B | 8GB | 8GB |
| 13B | 16GB | 16GB |
| 70B | 64GB | 40GB+ |

### Response Times (7B model, RTX 3090)

| Operation | Time |
|-----------|------|
| First token | ~500ms |
| Full response (500 tokens) | ~5s |

## Comparison with OpenAI

| Aspect | Ollama | OpenAI |
|--------|--------|--------|
| 成本 | 免費（硬體成本） | 按 token 計費 |
| 延遲 | 本地，低延遲 | 網路延遲 |
| 品質 | 依模型，通常略低 | 最佳品質 |
| 隱私 | 資料不出境 | 資料發送到雲端 |
| 離線 | 支援 | 不支援 |

## Related

- [[LLM Reasoner]] - 使用 Ollama 的組件
- [[OpenAI API]] - 雲端替代方案
- [[DCGM Exporter]] - GPU 監控

## References

- [Ollama Documentation](https://ollama.ai/docs)
- [Ollama Model Library](https://ollama.ai/library)
- [Ollama GitHub](https://github.com/ollama/ollama)
