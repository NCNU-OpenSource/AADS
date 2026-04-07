"""
LLM Client Base Interface

Defines the abstract interface for LLM clients.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class LLMMessage:
    """LLM message structure"""
    role: str  # 'system', 'user', 'assistant'
    content: str


@dataclass
class LLMResponse:
    """LLM response structure"""
    content: str
    model: str
    tokens_used: int
    finish_reason: str


class BaseLLMClient(ABC):
    """
    Abstract base class for LLM clients

    Implementations:
    - OpenAICompatibleClient: OpenAI-compatible APIs (OpenAI, NEW API, etc.)
    - OllamaClient: Local Ollama server
    """

    @abstractmethod
    async def complete(
        self,
        messages: List[LLMMessage],
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> LLMResponse:
        """
        Generate completion from messages

        Args:
            messages: List of messages
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens to generate

        Returns:
            LLMResponse object
        """
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Get the model name"""
        pass
