"""Системные промпты трёх уровней (T-014 / T-029).

Источник истины — таблица `prompts` (итерация 17). На старте приложения
она сидируется текущими `.txt`-файлами и прогревает in-memory кэш. Если
БД недоступна, загрузчик падает обратно на файлы — приложение не должно
терять возможность комментировать из-за сбоя prompt-хранилища.

Кэш сбрасывается `reset_prompt_cache()`: следующий `load_system_prompt`
читает строку из БД заново. Это даёт горячую правку промпта в админке без
рестарта. Мультипроцессная инвалидация (Redis Pub/Sub) — следующий шаг
T-029.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, cast

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Prompt
from app.errors import InvalidLevelError

__all__ = [
    "PROMPT_LEVELS",
    "load_system_prompt",
    "reset_prompt_cache",
    "seed_prompts",
    "warm_prompt_cache",
]

logger = structlog.get_logger(__name__)

#: Уровни, для которых есть промпты.
PROMPT_LEVELS = frozenset({"beginner", "club", "gm"})

#: In-memory кэш `level -> text`; заполняется из БД или файлов.
_cache: dict[str, str] = {}


def _validate_level(level: str) -> None:
    if level not in PROMPT_LEVELS:
        expected = ", ".join(sorted(PROMPT_LEVELS))
        raise InvalidLevelError(
            f"Нет промпта для уровня {level!r}; ожидается один из: {expected}"
        )


def _read_file(level: str) -> str:
    resource = resources.files("app.prompts").joinpath(f"{level}.txt")
    return resource.read_text(encoding="utf-8").strip()


def reset_prompt_cache(level: str | None = None) -> None:
    """Сбросить кэш промпта (всего или одного уровня)."""
    if level is None:
        _cache.clear()
    else:
        _cache.pop(level, None)


async def load_system_prompt(
    level: str, session: AsyncSession | None = None
) -> str:
    """Вернуть системный промпт уровня.

    Сначала смотрит в in-memory кэш, затем в таблицу `prompts`; при
    недоступности БД/пустой строке — читает `.txt`-файл.

    Args:
        level: ``beginner`` / ``club`` / ``gm``.
        session: открытая сессия запроса (чтобы не создавать новую).

    Raises:
        InvalidLevelError: неизвестный уровень.
        FileNotFoundError: нет ни строки в БД, ни файла.
    """
    _validate_level(level)
    cached = _cache.get(level)
    if cached is not None:
        return cached

    try:
        if session is not None:
            row = await session.scalar(
                select(Prompt).where(Prompt.level == level)
            )
        else:
            row = await _fetch_prompt(level)
        if row is not None and row.content.strip():
            _cache[level] = row.content.strip()
            return _cache[level]
        if row is not None:
            logger.warning("prompt_empty_in_db", level=level)
    except Exception as exc:  # БД недоступна — не ломаем генерацию
        logger.warning(
            "prompt_db_load_failed", level=level, error=str(exc)
        )

    text = _read_file(level)
    _cache[level] = text
    return text


async def _fetch_prompt(level: str) -> Prompt | None:
    """Прочитать строку промпта во временном NullPool-движке.

    Нужен, когда вызывающий не передал сессию (скрипт/тест): глобальный
    `SessionLocal` может быть привязан к закрытому event loop после
    предыдущего TestClient.
    """
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as own_session:
            return await own_session.scalar(
                select(Prompt).where(Prompt.level == level)
            )
    finally:
        await engine.dispose()


async def seed_prompts(session: AsyncSession) -> int:
    """Засидить `prompts` файлами, если строк ещё нет (idempotent).

    Returns:
        Сколько строк добавлено.
    """
    existing = set(
        (await session.scalars(select(Prompt.level))).all()
    )
    added = 0
    for level in sorted(PROMPT_LEVELS):
        if level in existing:
            continue
        content = _read_file(level)
        # ON CONFLICT: два воркера могут стартовать одновременно и
        # попытаться засидить один и тот же уровень.
        statement = (
            pg_insert(Prompt)
            .values(level=level, content=content, version=1)
            .on_conflict_do_nothing(index_elements=["level"])
        )
        result = await session.execute(statement)
        added += int(cast(CursorResult[Any], result).rowcount or 0)
    if added:
        await session.commit()
        logger.info("prompts_seeded", added=added)
    return added


async def warm_prompt_cache(session: AsyncSession) -> int:
    """Прочитать все промпты из БД в in-memory кэш."""
    rows = (await session.scalars(select(Prompt))).all()
    for row in rows:
        if row.level in PROMPT_LEVELS and row.content.strip():
            _cache[row.level] = row.content.strip()
    logger.info("prompts_cache_warmed", levels=sorted(_cache))
    return len(_cache)
