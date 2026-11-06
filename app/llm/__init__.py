"""LLM-адаптер: локальная LM Studio, облачный DeepSeek и роутер (T-013, T-123)."""

from __future__ import annotations

from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.llm.factory import LOCAL_LEVELS, get_llm_client, resolve_llm_provider
from app.llm.openai_client import OpenAICompatibleClient
from app.llm.router import BackendRouter, is_unavailable_error

__all__ = [
    "LOCAL_LEVELS",
    "BackendRouter",
    "BaseLLMClient",
    "LLMResult",
    "LLMUsage",
    "OpenAICompatibleClient",
    "get_llm_client",
    "is_unavailable_error",
    "resolve_llm_provider",
]
