"""
Ollama LLM Client

Local LLM client using Ollama server.
"""
import logging
from typing import List, Dict, Any
import aiohttp
import asyncio

from llm.base import BaseLLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


class OllamaClient(BaseLLMClient):
    """
    Ollama local LLM client

    Features:
    - Local inference (no API costs)
    - Privacy (data stays local)
    - Fast for small models
    """

    def __init__(
        self,
        base_url: str = "http://ollama:11434",
        model: str = "llama3.2:3b",
        timeout: int = 120
    ):
        """
        Initialize Ollama client

        Args:
            base_url: Ollama server URL
            model: Model name (e.g., 'llama3.2:3b', 'qwen2.5:7b')
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.timeout = timeout

        self.stats = {
            "total_requests": 0,
            "total_errors": 0
        }

        logger.info(f"Initialized Ollama client: {base_url}, model: {model}")

    async def complete(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """
        Generate completion using Ollama

        Args:
            messages: List of LLMMessage objects
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse object
        """
        url = f"{self.base_url}/api/chat"

        payload = {
            "model": self.model,
            "messages": [{"role": msg.role, "content": msg.content} for msg in messages],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Ollama request failed: {response.status} - {error_text}")
                        raise Exception(f"Ollama request failed: {response.status}")

                    data = await response.json()

                    # Extract response
                    content = data['message']['content']
                    tokens_used = data.get('eval_count', 0) + data.get('prompt_eval_count', 0)

                    # Update statistics
                    self.stats["total_requests"] += 1

                    logger.info(f"Ollama completion successful: ~{tokens_used} tokens")

                    return LLMResponse(
                        content=content,
                        model=self.model,
                        tokens_used=tokens_used,
                        finish_reason="stop"
                    )

        except asyncio.TimeoutError:
            logger.error("Ollama request timeout")
            self.stats["total_errors"] += 1
            raise

        except Exception as e:
            logger.error(f"Error during Ollama completion: {e}")
            self.stats["total_errors"] += 1
            raise

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
