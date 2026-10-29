"""Системные промпты трёх уровней (T-014 / T-029 / T-106).

Источник истины — таблица `prompts` (итерация 17). На старте приложения
она сидируется текущими `.txt`-файлами и прогревает in-memory кэш. Если
БД недоступна, загрузчик падает обратно на файлы — приложение не должно
терять возможность комментировать из-за сбоя prompt-хранилища.

T-106: для английского языка (`language="en"`) используются файлы
`<level>_en.txt`; в таблице `prompts` пока только русские строки, поэтому
английские промпты нельзя редактировать через админку. Выбор языка:
`language` запроса → `Accept-Language` → `DEFAULT_LANGUAGE`.

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
    "PROMPT_LANGUAGES",
    "PROMPT_LEVELS",
    "load_system_prompt",
    "reset_prompt_cache",
    "resolve_language",
    "seed_prompts",
    "warm_prompt_cache",
]

logger = structlog.get_logger(__name__)

#: Уровни, для которых есть промпты.
PROMPT_LEVELS = frozenset({"beginner", "club", "gm"})

#: Языки системных промптов (T-106). `ru` хранится в БД, `en` — файлами
#: `*_en.txt` (английские промпты пока не редактируются через админку).
PROMPT_LANGUAGES = frozenset({"ru", "en"})

#: In-memory кэш `(language, level) -> text`; заполняется из БД или файлов.
_cache: dict[tuple[str, str], str] = {}

#: Язык по умолчанию, если запрос/заголовок не задаёт поддерживаемый.
_DEFAULT_LANGUAGE = "ru"


def _validate_level(level: str) -> None:
    if level not in PROMPT_LEVELS:
        expected = ", ".join(sorted(PROMPT_LEVELS))
        raise InvalidLevelError(
            f"Нет промпта для уровня {level!r}; ожидается один из: {expected}"
        )


def _validate_language(language: str) -> None:
    if language not in PROMPT_LANGUAGES:
        expected = ", ".join(sorted(PROMPT_LANGUAGES))
        raise ValueError(
            f"Нет промпта для языка {language!r}; ожидается один из: {expected}"
        )


def resolve_language(
    requested: str | None,
    accept_language: str | None = None,
    default: str = _DEFAULT_LANGUAGE,
) -> str:
    """Выбрать язык комментария: параметр запроса → Accept-Language → default.

    Понимает значения вида ``en``, ``en-US``, ``en-US,en;q=0.9``. Неизвестный
    язык запроса игнорируется (а не роняет генерацию): лучше отдать текст на
    языке по умолчанию, чем 400 на каждый нестандартный заголовок.
    """
    for candidate in (requested, accept_language):
        normalized = _language_prefix(candidate)
        if normalized is not None:
            return normalized
    fallback = (default or _DEFAULT_LANGUAGE).strip().lower()
    return fallback if fallback in PROMPT_LANGUAGES else _DEFAULT_LANGUAGE


def _language_prefix(value: str | None) -> str | None:
    """Первый поддерживаемый язык из значения заголовка/параметра."""
    if not value:
        return None
    for part in value.split(","):
        primary = part.split(";")[0].strip().lower()
        if primary.startswith("en"):
            return "en"
        if primary.startswith("ru"):
            return "ru"
    return None


def _read_file(level: str, language: str = "ru") -> str:
    name = f"{level}.txt" if language == "ru" else f"{level}_{language}.txt"
    resource = resources.files("app.prompts").joinpath(name)
    return resource.read_text(encoding="utf-8").strip()


def reset_prompt_cache(level: str | None = None, language: str | None = None) -> None:
    """Сбросить кэш промптов (все/по уровню/по языку)."""
    if level is None and language is None:
        _cache.clear()
        return
    for key in list(_cache):
        cached_language, cached_level = key
        if (level is None or cached_level == level) and (
            language is None or cached_language == language
        ):
            _cache.pop(key, None)


async def load_system_prompt(
    level: str, session: AsyncSession | None = None, language: str = "ru"
) -> str:
    """Вернуть системный промпт уровня на нужном языке (T-106).

    Для ``ru`` сначала смотрит в in-memory кэш, затем в таблицу `prompts`;
    при недоступности БД/пустой строке — читает `.txt`-файл. Для ``en``
    используется файл ``<level>_en.txt`` (в БД английских строк нет).

    Args:
        level: ``beginner`` / ``club`` / ``gm``.
        session: открытая сессия запроса (чтобы не создавать новую).
        language: ``ru`` (по умолчанию) или ``en``.

    Raises:
        InvalidLevelError: неизвестный уровень.
        ValueError: неподдерживаемый язык.
        FileNotFoundError: нет ни строки в БД, ни файла.
    """
    _validate_level(level)
    _validate_language(language)
    cached = _cache.get((language, level))
    if cached is not None:
        return cached

    if language == "ru":
        try:
            if session is not None:
                row = await session.scalar(
                    select(Prompt).where(Prompt.level == level)
                )
            else:
                row = await _fetch_prompt(level)
            if row is not None and row.content.strip():
                _cache[(language, level)] = row.content.strip()
                return _cache[(language, level)]
            if row is not None:
                logger.warning("prompt_empty_in_db", level=level)
        except Exception as exc:  # БД недоступна — не ломаем генерацию
            logger.warning(
                "prompt_db_load_failed", level=level, error=str(exc)
            )

    text = _read_file(level, language)
    _cache[(language, level)] = text
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
            _cache[("ru", row.level)] = row.content.strip()
    logger.info(
        "prompts_cache_warmed",
        levels=sorted({level for _, level in _cache}),
    )
    return len(_cache)
