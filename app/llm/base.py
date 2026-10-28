"""Базовый интерфейс LLM-клиента (T-013).

Все провайдеры (локальная LM Studio, DeepSeek, в перспективе OpenAI)
говорят на одном OpenAI-совместимом протоколе, поэтому публичный контракт
один:

* :meth:`BaseLLMClient.generate` — текст ответа (то, что просит постановка);
* :meth:`BaseLLMClient.complete` — текст + расход токенов (нужен T-016 для
  `commentaries.tokens_in/tokens_out`);
* :meth:`BaseLLMClient.aclose` — закрыть HTTP-клиент.

Стоимость (`cost_usd`) считается в клиенте по ценам из настроек (T-031)
и переносится в `LLMUsage`, чтобы API мог записать её в `commentaries`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import TracebackType

__all__ = ["BaseLLMClient", "LLMResult", "LLMUsage"]


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Расход токенов одного запроса."""

    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    #: Оценка стоимости по прайсу провайдера (0 для локальной модели).
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Ответ модели: текст + расход."""

    text: str
    usage: LLMUsage


class BaseLLMClient(ABC):
    """Общий интерфейс клиента LLM.

    Args:
        model: имя модели в терминах провайдера.
        timeout_s: таймаут одного HTTP-запроса.
        max_retries: число повторов при 429/5xx/сетевых ошибках.
        retry_backoff_s: базовая пауза перед повтором (экспоненциально растёт).
    """

    @property
    def provider(self) -> str:
        """Имя провайдера для логов и метрик (переопределяется наследниками)."""
        return "base"

    def __init__(
        self,
        *,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        retry_backoff_s: float = 0.5,
    ) -> None:
        if not model:
            raise ValueError("model не может быть пустым")
        if timeout_s <= 0:
            raise ValueError("timeout_s должен быть > 0")
        if max_retries < 0:
            raise ValueError("max_retries должен быть ≥ 0")
        if retry_backoff_s < 0:
            raise ValueError("retry_backoff_s должен быть ≥ 0")
        self._model = model
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s
        # Уровень ассистента выставляет вызывающий сервис; нужен только для
        # атрибута спана `llm.level` (T-030) и не влияет на запрос.
        self._level: str | None = None

    @property
    def model(self) -> str:
        """Имя модели, с которой работает клиент."""
        return self._model

    @property
    def level(self) -> str | None:
        """Уровень ассистента, если вызывающий его указал."""
        return self._level

    def set_level(self, level: str | None) -> None:
        """Запомнить уровень ассистента для OTel-спана (`llm.level`)."""
        self._level = level

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def max_retries(self) -> int:
        return self._max_retries

    @property
    def retry_backoff_s(self) -> float:
        return self._retry_backoff_s

    @abstractmethod
    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        """Отправить промпт и вернуть текст + расход токенов."""

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Отправить промпт и вернуть только текст ответа."""
        result = await self.complete(prompt, system_prompt)
        return result.text

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Отдавать текст ответа чанками (SSE).

        Базовая реализация не умеет стримить и отдаёт весь ответ одним
        чанком — этого достаточно для фейков в тестах. Реальные клиенты
        переопределяют метод.
        """
        result = await self.complete(prompt, system_prompt)
        if result.text:
            yield result.text

    async def aclose(self) -> None:
        """Освободить ресурсы (по умолчанию нечего освобождать)."""
        return None

    async def __aenter__(self) -> BaseLLMClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
