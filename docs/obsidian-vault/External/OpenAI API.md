---
title: OpenAI API
type: external
layer: external
tags: [external, llm, openai, gpt]
created: 2026-04-08
---

# OpenAI API

## Overview

OpenAI API 提供 GPT 系列大型語言模型，用於高品質的根因分析。

## Role in System

- 為 [[LLM Reasoner]] 提供深度分析能力
- 處理高嚴重度異常（cascade 策略）
- 生成結構化診斷報告

## Configuration

### Environment Variables

| Variable | Example | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | `sk-...` | API Key |
| `OPENAI_MODEL` | `gpt-4-turbo-preview` | 模型名稱 |
| `OPENAI_MAX_TOKENS` | `2000` | 最大回應長度 |

### Model Options

| Model | Context | Best For |
|-------|---------|----------|
| `gpt-4-turbo-preview` | 128k | 複雜分析，長上下文 |
| `gpt-4` | 8k | 標準分析 |
| `gpt-3.5-turbo` | 16k | 快速分類，成本敏感 |

## Client Implementation

**位置:** `layer2-analyzer/src/llm/openai_compatible.py`

```python
import openai
import json
from typing import Optional

class OpenAIClient:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4-turbo-preview",
        max_tokens: int = 2000
    ):
        self.client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
    
    async def complete(self, prompt: str) -> dict:
        """Generate completion with JSON response"""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert SRE. Always respond in valid JSON."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=self.max_tokens,
            temperature=0.3
        )
        
        content = response.choices[0].message.content
        return json.loads(content)
    
    async def classify(self, text: str, categories: list[str]) -> str:
        """Quick classification"""
        prompt = f"""Classify the following text into one of these categories: {categories}

Text: {text}

Respond with JSON: {{"category": "..."}}"""
        
        result = await self.complete(prompt)
        return result.get("category", categories[0])
```

## API Usage

### Request Format

```python
response = await client.chat.completions.create(
    model="gpt-4-turbo-preview",
    messages=[
        {"role": "system", "content": "..."},
        {"role": "user", "content": "..."}
    ],
    response_format={"type": "json_object"},
    max_tokens=2000
)
```

### Response Format

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gpt-4-turbo-preview",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "{\"root_cause\": \"...\", ...}"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 500,
    "completion_tokens": 200,
    "total_tokens": 700
  }
}
```

## Cost Management

### Token Pricing (GPT-4 Turbo)
- Input: $0.01 / 1K tokens
- Output: $0.03 / 1K tokens

### Optimization Strategies

1. **Cascade Strategy:** 先用本地 LLM 分類，只對高嚴重度使用 OpenAI
2. **Prompt Compression:** 精簡 prompt，移除冗餘資訊
3. **Caching:** 相似異常使用快取結果
4. **Batching:** 合併相關異常一次分析

## Error Handling

```python
import openai

try:
    response = await client.complete(prompt)
except openai.RateLimitError:
    # 等待後重試
    await asyncio.sleep(60)
    response = await client.complete(prompt)
except openai.APIError as e:
    # 回退到本地 LLM
    response = await local_client.complete(prompt)
```

## Related

- [[LLM Reasoner]] - 使用 OpenAI 的組件
- [[Ollama]] - 本地替代方案
- [[Layer 2 - Root Cause Analysis]]

## References

- [OpenAI API Documentation](https://platform.openai.com/docs)
- [OpenAI Pricing](https://openai.com/pricing)
- [Best Practices](https://platform.openai.com/docs/guides/production-best-practices)
