"""Кэш retrieval-результатов по позиции (T-018, итерация 66).

`annotated_games` — единственный источник текста аннотаций, но поиск по
косинусному расстоянию требует эмбеддинга запроса (сетевой вызов к LM
Studio). Таблица `retrieval_cache` хранит `top_k_ids` — идентификаторы уже
найденных аннотаций для данного FEN. Повторный запрос той же позиции
читает строки по `id` и не вызывает embedding-модель.

Ключ — sha256 канонического FEN; TTL — `RETRIEVAL_CACHE_TTL_DAYS` (30 дней,
по аналогии с `ENGINE_CACHE_TTL_DAYS`). Кэш best-effort: ошибка чтения/записи
только логируется через structlog и не ломает RAG-поиск.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import RetrievalCache as RetrievalCacheRow
from app.db.session import SessionLocal
from app.ingestion.parser import parse_fen

__all__ = ["RetrievalCache", "retrieval_fen_hash"]

logger = structlog.get_logger(__name__)


def retrieval_fen_hash(fen: str) -> str:
    """sha256 FEN без номера полного хода — ключ в `retrieval_cache`.

    Как и в `engine_cache`, первые пять полей FEN (расстановка, сторона,
    рокировки, en passant, счётчик полуходов) однозначно задают позицию;
    номер хода влияет только на запись в кэш при том же расположении.
    """
    canonical = parse_fen(fen).fen()
    position = " ".join(canonical.split(" ")[:5])
    return hashlib.sha256(position.encode("utf-8")).hexdigest()


class RetrievalCache:
    """Чтение/запись `retrieval_cache` (best-effort, отдельные сессии).

    Args:
        settings: настройки; по умолчанию `get_settings()`.
        session_factory: фабрика сессий; по умолчанию `SessionLocal`.
            Явно передаётся в тестах для проверки graceful degradation.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        cfg = settings or get_settings()
        if cfg.retrieval_cache_ttl_days < 1:
            raise ValueError("retrieval_cache_ttl_days должен быть ≥ 1")
        self._settings = cfg
        self._session_factory = session_factory or SessionLocal

    @property
    def enabled(self) -> bool:
        """Включён ли кэш настройками."""
        return self._settings.retrieval_cache_enabled

    @property
    def ttl_days(self) -> int:
        """Срок жизни записи в днях."""
        return self._settings.retrieval_cache_ttl_days

    def _cutoff(self, *, now: datetime | None = None) -> datetime:
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment - timedelta(days=self.ttl_days)

    async def lookup(self, fen: str) -> list[int] | None:
        """Вернуть сохранённые `top_k_ids` или ``None``.

        ``None`` означает промах: нет записи, запись старше TTL, выключенный
        кэш или ошибка БД. Ошибка только логируется — RAG продолжит поиск
        через embedding-модель.
        """
        if not self.enabled:
            return None
        try:
            fen_key = retrieval_fen_hash(fen)
            async with self._session_factory() as session:
                row = await session.scalar(
                    select(RetrievalCacheRow)
                    .where(
                        RetrievalCacheRow.fen_hash == fen_key,
                        RetrievalCacheRow.created_at >= self._cutoff(),
                    )
                    .order_by(RetrievalCacheRow.created_at.desc())
                    .limit(1)
                )
            if row is None or not row.top_k_ids:
                return None
            ids = [int(item) for item in row.top_k_ids]
        except (DBAPIError, OSError, ValueError) as exc:
            logger.warning("retrieval_cache_lookup_failed", error=str(exc))
            return None
        if not ids:
            return None
        logger.debug("retrieval_cache_hit", ids=len(ids))
        return ids

    async def store(self, fen: str, ids: list[int]) -> None:
        """Сохранить `top_k_ids` (best-effort).

        У `retrieval_cache` нет уникального ограничения на `fen_hash`
        (только индекс), поэтому обновление — это DELETE + INSERT в одной
        транзакции: последняя запись побеждает. Ошибка только логируется.
        """
        if not self.enabled or not ids:
            return
        try:
            fen_key = retrieval_fen_hash(fen)
            async with self._session_factory() as session:
                await session.execute(
                    delete(RetrievalCacheRow).where(
                        RetrievalCacheRow.fen_hash == fen_key
                    )
                )
                session.add(
                    RetrievalCacheRow(
                        fen_hash=fen_key,
                        top_k_ids=[int(item) for item in ids],
                        created_at=datetime.now(UTC),
                    )
                )
                await session.commit()
        except (DBAPIError, OSError) as exc:
            logger.warning("retrieval_cache_store_failed", error=str(exc))

    async def cleanup_expired(self) -> int:
        """Удалить записи старше TTL; вернуть число удалённых строк.

        Raises:
            DBAPIError: БД недоступна (вызывающий решает, логировать ли).
        """
        async with self._session_factory() as session:
            result = await session.execute(
                delete(RetrievalCacheRow).where(
                    RetrievalCacheRow.created_at < self._cutoff()
                )
            )
            await session.commit()
        return int(getattr(result, "rowcount", 0) or 0)
