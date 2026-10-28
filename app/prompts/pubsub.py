"""Redis Pub/Sub для инвалидации кэша промптов (T-029/KI-032).

Один процесс редактирует промпт в админке и публикует `level` (или `*`);
каждый воркер слушает канал и сбрасывает свой in-memory кэш. Если Redis
недоступен, publish логирует warning, а локальный `reset_prompt_cache`
уже выполнен вызывающей стороной — приложение продолжает работать в
однопроцессном режиме.
"""

from __future__ import annotations

import asyncio
from typing import Any

import redis.asyncio as aioredis
import structlog

from app.prompts import reset_prompt_cache

__all__ = [
    "PROMPT_UPDATES_CHANNEL",
    "handle_prompt_update",
    "publish_prompt_update",
    "watch_prompt_updates",
]

logger = structlog.get_logger(__name__)

#: Канал, в который публикуются изменения промптов.
PROMPT_UPDATES_CHANNEL = "prompts_updates"

#: `*` означает «сбросить весь кэш».
_RESET_ALL = "*"


async def publish_prompt_update(
    redis: aioredis.Redis,
    level: str | None,
    *,
    channel: str = PROMPT_UPDATES_CHANNEL,
) -> bool:
    """Опубликовать изменение промпта; ``False`` при ошибке Redis."""
    try:
        await redis.publish(channel, level or _RESET_ALL)
        return True
    except Exception as exc:  # Redis недоступен — не ломаем админку
        logger.warning(
            "prompt_update_publish_failed",
            channel=channel,
            level=level,
            error=str(exc),
        )
        return False


async def handle_prompt_update(message: dict[str, Any]) -> None:
    """Применить одно Pub/Sub-сообщение к локальному кэшу."""
    data: Any = message.get("data")
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if data in (None, "", b"", _RESET_ALL):
        reset_prompt_cache()
        logger.info("prompt_cache_invalidated", level=_RESET_ALL)
        return
    level = str(data)
    reset_prompt_cache(level)
    logger.info("prompt_cache_invalidated", level=level)


async def watch_prompt_updates(
    redis: aioredis.Redis,
    *,
    channel: str = PROMPT_UPDATES_CHANNEL,
    poll_timeout_s: float = 1.0,
) -> None:
    """Слушать канал до отмены задачи (запускается в lifespan).

    При недоступном Redis задача завершается с warning: локальный кэш
    продолжает работать, мультипроцессная инвалидация просто недоступна.
    """
    pubsub = redis.pubsub()
    try:
        await pubsub.subscribe(channel)
        logger.info("prompt_updates_subscribed", channel=channel)
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=poll_timeout_s,
            )
            if message:
                await handle_prompt_update(message)
            await asyncio.sleep(0.05)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "prompt_updates_unavailable", channel=channel, error=str(exc)
        )
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:
            pass
        try:
            # redis-py 5.x: PubSub.aclose без аннотации (upstream, KI-004).
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        except Exception:
            pass
