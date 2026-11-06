"""Маршрутизация LLM-бэкендов: local-first с fallback в облако (T-123).

Клубный уровень обслуживает локальная LM Studio (или vLLM за reverse
SSH-туннелем из T-123); если локальный GPU недоступен, запрос мгновенно
уходит в облачный DeepSeek. Это закрывает требование SPEC §10
«fallback локальная LLM → облачная» и готовит почву для T-123.

Гарантии:

* **Fallback только на недоступность.** `httpx.ConnectError`/таймауты и
  `LLMTimeoutError` (включая обёрнутые клиентом в `LLMError` причины)
  переключают на резервный бэкенд; HTTP 4xx и «кривой ответ» — это не
  проблема доступности, они пробрасываются наружу.
* **Circuit-breaker.** После сбоя primary пропускается `cooldown_s` секунд:
  каждый запрос не ждёт мёртвый GPU по 2 секунды. По истечении cooldown
  primary пробуется снова, успех закрывает цепь.
* **Приватность.** Роутер включается только для уровня `club`
  (`app.llm.factory`); `beginner` остаётся строго локальным (RULES §7.3).
* **Логи без текстов промптов:** `local_gpu_unavailable`,
  `llm_router_circuit_open`, `llm_router_primary_recovered`,
  `llm_router_fallback_disabled`.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable, Sequence

import httpx
import structlog

from app.errors import LLMTimeoutError
from app.llm.base import BaseLLMClient, LLMResult

__all__ = ["BackendRouter", "is_unavailable_error", "log_fallback_disabled"]

logger = structlog.get_logger(__name__)

#: Исключения, означающие «бэкенд недоступен», а не «ответ неверный».
_UNAVAILABLE_TYPES: tuple[type[BaseException], ...] = (
    LLMTimeoutError,
    httpx.ConnectError,
    httpx.TimeoutException,
)

#: Сколько раз идти по цепочке причин (`raise ... from exc`).
_MAX_CAUSE_DEPTH = 8


def is_unavailable_error(exc: BaseException) -> bool:
    """Является ли ошибка недоступностью бэкенда (а не плохим ответом).

    `OpenAICompatibleClient` оборачивает httpx-ошибки в `LLMError` /
    `LLMTimeoutError` через `raise ... from exc`, поэтому проверяется и
    цепочка `__cause__`/`__context__`.

    Args:
        exc: исключение, пойманное при вызове бэкенда.

    Returns:
        ``True`` для `httpx.ConnectError`, таймаутов httpx и
        `LLMTimeoutError` (в том числе в цепочке причин).
    """
    if isinstance(exc, _UNAVAILABLE_TYPES):
        return True
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < _MAX_CAUSE_DEPTH:
        if id(current) in seen:
            break
        seen.add(id(current))
        cause = current.__cause__ or current.__context__
        if cause is not None and isinstance(cause, _UNAVAILABLE_TYPES):
            return True
        current = cause
        depth += 1
    return False


def log_fallback_disabled(*, level: str, reason: str) -> None:
    """Зафиксировать, что резервный бэкенд не настроен (нет ключа облака)."""
    logger.warning("llm_router_fallback_disabled", level=level, reason=reason)


class BackendRouter(BaseLLMClient):
    """Последовательный роутер бэкендов с fallback и circuit-breaker.

    Args:
        backends: бэкенды в порядке приоритета; первый — primary.
            Для `club` это ``[local, deepseek]``.
        cooldown_s: сколько секунд не пробовать primary после сбоя
            (0 — пробовать каждый раз).
        clock: источник монотонного времени (тесты подставляют управляемые
            часы).

    Raises:
        ValueError: список бэкендов пуст или cooldown отрицателен.
    """

    def __init__(
        self,
        backends: Sequence[BaseLLMClient],
        *,
        cooldown_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not backends:
            raise ValueError("BackendRouter требует хотя бы один бэкенд")
        if cooldown_s < 0:
            raise ValueError("cooldown_s должен быть ≥ 0")
        self._backends = tuple(backends)
        primary = self._backends[0]
        super().__init__(
            model=primary.model,
            timeout_s=primary.timeout_s,
            max_retries=primary.max_retries,
            retry_backoff_s=primary.retry_backoff_s,
        )
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._open_until: float | None = None

    # --- публичное ------------------------------------------------------

    @property
    def provider(self) -> str:
        """Провайдер primary: для `club` снаружи это по-прежнему lmstudio."""
        return self._backends[0].provider

    @property
    def backends(self) -> tuple[BaseLLMClient, ...]:
        """Все бэкенды в порядке приоритета."""
        return self._backends

    @property
    def primary(self) -> BaseLLMClient:
        """Основной бэкенд (локальный GPU)."""
        return self._backends[0]

    @property
    def fallback(self) -> BaseLLMClient | None:
        """Резервный бэкенд (облако) или ``None``."""
        return self._backends[1] if len(self._backends) > 1 else None

    def set_level(self, level: str | None) -> None:
        """Запомнить уровень и пробросить его во все бэкенды."""
        super().set_level(level)
        for backend in self._backends:
            backend.set_level(level)

    async def aclose(self) -> None:
        """Закрыть все бэкенды."""
        for backend in self._backends:
            await backend.aclose()

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        """Выполнить запрос с local-first fallback.

        Raises:
            LLMError: резервный бэкенд тоже ответил ошибкой.
            Exception: любая не-соединенческая ошибка primary (HTTP 4xx,
                кривой ответ) — без fallback.
        """
        fallback = self.fallback
        if fallback is not None and self._circuit_open():
            logger.info(
                "llm_router_circuit_open",
                primary=self.primary.provider,
                fallback=fallback.provider,
            )
            return await fallback.complete(prompt, system_prompt)

        try:
            result = await self.primary.complete(prompt, system_prompt)
        except Exception as exc:
            if fallback is None or not is_unavailable_error(exc):
                raise
            self._report_unavailable(exc)
            return await fallback.complete(prompt, system_prompt)

        self._close_circuit()
        return result

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        """Стримить ответ с fallback, пока не отдан первый чанк.

        Если primary упал **до** первого чанка — переключаемся на fallback.
        Если чанки уже отданы, повторять поток нельзя (дублирование текста):
        ошибка пробрасывается потребителю.
        """
        fallback = self.fallback
        if fallback is not None and self._circuit_open():
            logger.info(
                "llm_router_circuit_open",
                primary=self.primary.provider,
                fallback=fallback.provider,
            )
            async for chunk in fallback.stream_generate(prompt, system_prompt):
                yield chunk
            return

        yielded = False
        try:
            async for chunk in self.primary.stream_generate(prompt, system_prompt):
                yielded = True
                yield chunk
        except Exception as exc:
            if yielded or fallback is None or not is_unavailable_error(exc):
                raise
            self._report_unavailable(exc)
        else:
            self._close_circuit()
            return

        async for chunk in fallback.stream_generate(prompt, system_prompt):
            yield chunk

    # --- внутреннее -----------------------------------------------------

    def _circuit_open(self) -> bool:
        if self._open_until is None:
            return False
        return self._clock() < self._open_until

    def _open_circuit(self) -> None:
        if self._cooldown_s > 0:
            self._open_until = self._clock() + self._cooldown_s
        else:
            self._open_until = None

    def _close_circuit(self) -> None:
        if self._open_until is None:
            return
        self._open_until = None
        logger.info(
            "llm_router_primary_recovered", primary=self.primary.provider
        )

    def _report_unavailable(self, exc: BaseException) -> None:
        """Открыть цепь и залогировать недоступность local GPU."""
        self._open_circuit()
        fallback = self.fallback
        logger.warning(
            "local_gpu_unavailable",
            primary=self.primary.provider,
            fallback=fallback.provider if fallback is not None else None,
            error=type(exc).__name__,
            cooldown_s=self._cooldown_s,
        )
