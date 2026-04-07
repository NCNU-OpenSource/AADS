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
        # If base_url already ends with a known endpoint (e.g., /responses),
        # use it directly instead of appending /chat/completions
        if self.base_url.endswith('/responses'):
            url = self.base_url
        else:
            url = f"{self.base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        # Different payload format for /responses endpoint
        if self.base_url.endswith('/responses'):
            # Convert messages to a single input string
            input_text = "\n\n".join([
                f"{msg.role}: {msg.content}"
                for msg in messages
            ])
            payload = {
                "model": self.model,
                "input": input_text,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
        else:
            # Standard OpenAI format
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

                        # Log the response structure for debugging
                        logger.info(f"API response structure: {list(data.keys())}")
                        logger.debug(f"Full API response: {data}")

                        # Extract response (support both OpenAI format and custom /responses format)
                        if 'choices' in data:
                            # Standard OpenAI format
                            choice = data['choices'][0]
                            content = choice['message']['content']
                            finish_reason = choice.get('finish_reason', 'unknown')
                            tokens_used = data.get('usage', {}).get('total_tokens', 0)
                        elif 'output' in data:
                            # NEW API /responses format
                            raw_output = data['output']
                            # Extract text from nested structure: output[0]['content'][0]['text']
                            if isinstance(raw_output, list) and len(raw_output) > 0:
                                message = raw_output[0]
                                if isinstance(message, dict) and 'content' in message:
                                    content_items = message['content']
                                    if isinstance(content_items, list) and len(content_items) > 0:
                                        # Find the first output_text item
                                        for item in content_items:
                                            if item.get('type') == 'output_text' and 'text' in item:
                                                content = item['text']
                                                break
                                        else:
                                            # Fallback: use string representation
                                            content = str(content_items[0].get('text', raw_output))
                                    else:
                                        content = str(message.get('content', raw_output))
                                else:
                                    content = str(raw_output)
                            else:
                                content = str(raw_output)
                            finish_reason = data.get('status', 'stop')
                            tokens_used = data.get('usage', {}).get('total_tokens', 0)
                        elif 'text' in data:
                            # Alternative format
                            raw_text = data['text']
                            # Handle both list and string formats
                            if isinstance(raw_text, list):
                                content = '\n'.join(str(item) for item in raw_text)
                            else:
                                content = str(raw_text)
                            finish_reason = 'stop'
                            tokens_used = data.get('usage', {}).get('total_tokens', 0)
                        else:
                            # Unknown format, log and raise error
                            logger.error(f"Unknown API response format. Keys: {list(data.keys())}")
                            raise ValueError(f"Unknown API response format: {list(data.keys())}")

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
