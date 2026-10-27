"""LLM-адаптер: локальная LM Studio и облачный DeepSeek (T-013)."""

from __future__ import annotations

from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.llm.factory import LOCAL_LEVELS, get_llm_client, resolve_llm_provider
from app.llm.openai_client import OpenAICompatibleClient

__all__ = [
    "LOCAL_LEVELS",
    "BaseLLMClient",
    "LLMResult",
    "LLMUsage",
    "OpenAICompatibleClient",
    "get_llm_client",
    "resolve_llm_provider",
]
