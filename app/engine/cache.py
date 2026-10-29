"""Кэш результатов Stockfish по позиции (T-108, итерация 30).

Проблема: при анализе похожих партий (одни и те же дебютные позиции,
разбор одного и того же PGN дважды) Stockfish заново считает позиции,
которые уже считались. Кэш хранит готовые MultiPV-линии и отдаёт их без
запуска поиска.

Ключ — `(fen_hash, engine_version, depth, multipv)`:

* `fen_hash` — sha256 канонического FEN **без номера хода** (первые пять
  полей: расстановка, сторона, рокировки, en passant, счётчик полуходов).
  Позиция, достигнутая в другой партии/на другом ходу, получает тот же
  ключ; счётчик полуходов оставлен, потому что влияет на правило 50 ходов.
* `engine_version` — UCI `id name` (например, ``Stockfish 17``): после
  обновления бинарника кэш не должен переиспользовать старые оценки.
* `depth`/`multipv` — параметры поиска уровня; `beginner` (12/1) и `gm`
  (28/5) никогда не смешиваются.

Кэш — best-effort: недоступная БД или битая строка только логируются,
анализ продолжается обычным поиском. TTL (`ENGINE_CACHE_TTL_DAYS`, по
умолчанию 30 дней) проверяется на чтении, устаревшие строки удаляет
`cleanup_expired` при старте приложения.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError

from app.config import Settings, get_settings
from app.db.models import EngineCacheEntry
from app.db.session import SessionLocal
from app.engine.models import EngineLine
from app.ingestion.parser import parse_fen

__all__ = [
    "EngineCache",
    "EngineCacheKey",
    "engine_fen_hash",
    "normalize_fen_for_cache",
]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EngineCacheKey:
    """Ключ записи кэша движка."""

    fen_hash: str
    engine_version: str
    depth: int
    multipv: int


def normalize_fen_for_cache(fen: str) -> str:
    """Канонический FEN без номера хода (влияет на ключ кэша).

    Оставляет пять полей FEN: позиция, сторона хода, рокировки, en passant
    и счётчик полуходов. Номер полного хода не влияет ни на оценку, ни на
    правило 50 ходов, поэтому в ключ не входит.
    """
    canonical = parse_fen(fen).fen()
    return " ".join(canonical.split(" ")[:5])


def engine_fen_hash(fen: str) -> str:
    """sha256 нормализованного FEN — ключ позиции в `engine_cache`."""
    normalized = normalize_fen_for_cache(fen)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class EngineCache:
    """Чтение/запись `engine_cache` (best-effort, отдельные сессии).

    Args:
        settings: настройки; по умолчанию `get_settings()`.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        cfg = settings or get_settings()
        if cfg.engine_cache_ttl_days < 1:
            raise ValueError("engine_cache_ttl_days должен быть ≥ 1")
        self._settings = cfg

    @property
    def ttl_days(self) -> int:
        """Срок жизни записи в днях."""
        return self._settings.engine_cache_ttl_days

    def _cutoff(self, *, now: datetime | None = None) -> datetime:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment - timedelta(days=self.ttl_days)

    async def lookup(self, key: EngineCacheKey) -> list[EngineLine] | None:
        """Вернуть закэшированные линии или ``None``.

        Устаревшие записи (старше TTL) считаются промахом. Ошибка БД не
        пробрасывается: анализ просто пойдёт в Stockfish.
        """
        try:
            async with SessionLocal() as session:
                row = await session.scalar(
                    select(EngineCacheEntry)
                    .where(
                        EngineCacheEntry.fen_hash == key.fen_hash,
                        EngineCacheEntry.engine_version
                        == key.engine_version,
                        EngineCacheEntry.depth == key.depth,
                        EngineCacheEntry.multipv == key.multipv,
                        EngineCacheEntry.created_at >= self._cutoff(),
                    )
                    .limit(1)
                )
            if row is None:
                return None
            lines = [
                EngineLine.model_validate(item) for item in row.lines
            ]
        except (DBAPIError, OSError, ValueError) as exc:
            logger.warning(
                "engine_cache_lookup_failed",
                error=str(exc),
                engine_version=key.engine_version,
            )
            return None
        logger.debug(
            "engine_cache_hit",
            engine_version=key.engine_version,
            depth=key.depth,
            multipv=key.multipv,
        )
        return lines

    async def store(
        self, key: EngineCacheKey, lines: list[EngineLine]
    ) -> None:
        """Сохранить результат поиска (upsert по ключу).

        Ошибка записи только логируется: кэш не должен ломать анализ.
        """
        if not lines:
            return
        try:
            async with SessionLocal() as session:
                statement = pg_insert(EngineCacheEntry).values(
                    fen_hash=key.fen_hash,
                    engine_version=key.engine_version,
                    depth=key.depth,
                    multipv=key.multipv,
                    lines=[
                        line.model_dump(mode="json") for line in lines
                    ],
                    created_at=datetime.now(UTC),
                )
                statement = statement.on_conflict_do_update(
                    index_elements=[
                        "fen_hash",
                        "engine_version",
                        "depth",
                        "multipv",
                    ],
                    set_={
                        "lines": statement.excluded.lines,
                        "created_at": statement.excluded.created_at,
                    },
                )
                await session.execute(statement)
                await session.commit()
        except (DBAPIError, OSError) as exc:
            logger.warning(
                "engine_cache_store_failed",
                error=str(exc),
                engine_version=key.engine_version,
            )

    async def cleanup_expired(self) -> int:
        """Удалить записи старше TTL; вернуть число удалённых строк.

        Raises:
            DBAPIError: БД недоступна (вызывающий решает, логировать ли).
        """
        async with SessionLocal() as session:
            result = await session.execute(
                delete(EngineCacheEntry).where(
                    EngineCacheEntry.created_at < self._cutoff()
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
