"""Прогрев локальной LLM при старте приложения (T-014, KI-022).

LM Studio загружает модель в память при первом запросе: холодный старт —
≈ 12 с, тёплый ответ — ≈ 1 с. Поэтому в lifespan отправляется короткий
«пустой» запрос в фоне: к первому реальному комментарию модель уже готова.

Важно: прогрев **best-effort**. Нет LM Studio — приложение всё равно
стартует, в лог уходит `llm_warmup_failed`. Задача отменяется при
остановке приложения.
"""

from __future__ import annotations

import time

import structlog

from app.config import Settings, get_settings
from app.llm.base import BaseLLMClient
from app.llm.calls import llm_call_context
from app.llm.factory import get_llm_client

__all__ = ["WARMUP_PROMPT", "warmup_local_llm"]

logger = structlog.get_logger(__name__)

#: Короткий промпт: нужен только факт загрузки весов.
WARMUP_PROMPT = "Ответь одним словом: ok"


async def warmup_local_llm(
    settings: Settings | None = None,
    *,
    client: BaseLLMClient | None = None,
) -> bool:
    """Прогреть локальную модель (LM Studio).

    Args:
        settings: настройки (по умолчанию — из окружения).
        client: готовый клиент (тесты); если не передан — берётся `club`
            (локальный провайдер).

    Returns:
        ``True``, если модель ответила; ``False`` при любой ошибке.
        Исключения наружу не пробрасываются: прогрев не должен ломать старт.
    """
    cfg = settings or get_settings()
    owns_client = client is None
    llm = client
    started = time.perf_counter()
    try:
        if llm is None:
            llm = get_llm_client("club", cfg)
        # KI-046: прогрев — тоже вызов LLM и должен попадать в `llm_calls`
        # (operation="warmup"). HTTP-контекста здесь нет — recorder сам
        # сгенерирует trace_id (fallback).
        with llm_call_context(operation="warmup"):
            await llm.generate(WARMUP_PROMPT)
    except Exception as exc:
        logger.warning(
            "llm_warmup_failed", error=str(exc), provider=getattr(llm, "provider", None)
        )
        return False
    else:
        logger.info(
            "llm_warmup_ok",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            provider=getattr(llm, "provider", None),
        )
        return True
    finally:
        if owns_client and llm is not None:
            await llm.aclose()
