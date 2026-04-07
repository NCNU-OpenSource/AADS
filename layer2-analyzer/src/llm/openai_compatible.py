"""
OpenAI-Compatible LLM Client

Supports any OpenAI-compatible API with custom baseURL:
- OpenAI (https://api.openai.com/v1)
- NEW API (https://api.newapi.com/v1)
- Azure OpenAI
- DeepSeek
- Other compatible services
"""
import os
import logging
from typing import List, Dict, Any, Optional
import aiohttp

from llm.base import BaseLLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class OpenAICompatibleClient(BaseLLMClient):
    """
    OpenAI-compatible API client with custom baseURL support

    Features:
    - Custom baseURL for third-party APIs
    - Async HTTP requests
    - Automatic retry with exponential backoff
    - Token usage tracking
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        timeout: int = 60
    ):
        """
        Initialize OpenAI-compatible client

        Args:
            api_key: API key for authentication
            base_url: Base URL for API (supports custom endpoints)
            model: Model name to use
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout

        self.stats = {
            "total_requests": 0,
            "total_tokens": 0,
            "total_errors": 0
        }

        logger.info(f"Initialized OpenAI-compatible client: {base_url}, model: {model}")

    async def complete(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        retry: int = 3
    ) -> LLMResponse:
        """
        Generate completion from messages

        Args:
            messages: List of LLMMessage objects
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate
            retry: Number of retries on failure

        Returns:
            LLMResponse object
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": self.model,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        for attempt in range(retry):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=self.timeout)
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            logger.error(
                                f"API request failed (attempt {attempt + 1}/{retry}): "
                                f"{response.status} - {error_text}"
                            )
                            if attempt < retry - 1:
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            raise Exception(f"API request failed: {response.status} - {error_text}")

                        data = await response.json()

                        # Extract response
                        choice = data['choices'][0]
                        content = choice['message']['content']
                        finish_reason = choice.get('finish_reason', 'unknown')
                        tokens_used = data.get('usage', {}).get('total_tokens', 0)

                        # Update statistics
                        self.stats["total_requests"] += 1
                        self.stats["total_tokens"] += tokens_used

                        logger.info(
                            f"LLM completion successful: {tokens_used} tokens, "
                            f"finish_reason: {finish_reason}"
                        )

                        return LLMResponse(
                            content=content,
                            model=self.model,
                            tokens_used=tokens_used,
                            finish_reason=finish_reason
                        )

            except asyncio.TimeoutError:
                logger.error(f"Request timeout (attempt {attempt + 1}/{retry})")
                if attempt < retry - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self.stats["total_errors"] += 1
                    raise

            except Exception as e:
                logger.error(f"Error during LLM completion (attempt {attempt + 1}/{retry}): {e}")
                if attempt < retry - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    self.stats["total_errors"] += 1
                    raise

        # Should not reach here
        raise Exception("All retry attempts failed")

    def get_model_name(self) -> str:
        """Get the model name"""
        return self.model

    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics"""
        return {
            **self.stats,
            "model": self.model,
            "base_url": self.base_url
        }


# Import asyncio for exponential backoff
import asyncio
