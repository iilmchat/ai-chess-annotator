"""T-018: `retrieval_cache` — ключ, TTL и graceful degradation (unit)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.rag.cache import RetrievalCache, retrieval_fen_hash

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


class BrokenSession:
    """Сессия, которая падает при открытии (имитация недоступной БД)."""

    async def __aenter__(self) -> BrokenSession:
        raise OSError("database down")

    async def __aexit__(self, *args: object) -> bool:
        return False


def _broken_factory() -> Callable[[], AsyncSession]:
    return BrokenSession  # type: ignore[return-value]


def test_retrieval_fen_hash_ignores_fullmove_counter() -> None:
    first = retrieval_fen_hash(FEN)
    same_position_later = retrieval_fen_hash(FEN.replace(" 0 1", " 0 9"))
    other_position = retrieval_fen_hash(FEN.replace("KQkq", "KQ"))

    assert first == same_position_later
    assert first != other_position
    assert len(first) == 64


def test_ttl_must_be_positive() -> None:
    with pytest.raises(ValueError, match="retrieval_cache_ttl_days"):
        RetrievalCache(Settings(retrieval_cache_ttl_days=0))


@pytest.mark.asyncio()
async def test_disabled_cache_never_touches_database() -> None:
    cache = RetrievalCache(
        Settings(retrieval_cache_enabled=False), session_factory=_broken_factory()
    )

    assert await cache.lookup(FEN) is None
    await cache.store(FEN, [1, 2])


@pytest.mark.asyncio()
async def test_lookup_failure_degrades_to_none() -> None:
    cache = RetrievalCache(
        Settings(retrieval_cache_enabled=True), session_factory=_broken_factory()
    )

    assert await cache.lookup(FEN) is None
    # Запись тоже не пробрасывает ошибку наружу.
    await cache.store(FEN, [1, 2])


@pytest.mark.asyncio()
async def test_empty_ids_are_not_stored() -> None:
    calls: list[Any] = []

    def factory() -> Any:
        calls.append("session")
        return BrokenSession()

    cache = RetrievalCache(
        Settings(retrieval_cache_enabled=True), session_factory=factory
    )

    await cache.store(FEN, [])
    assert calls == []
