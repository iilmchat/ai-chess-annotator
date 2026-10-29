"""Интеграционные тесты кэша результатов Stockfish (T-108).

Проверяем:

* повторный `EngineService.analyze` той же позиции не вызывает движок
  (fake UCI-процесс считает вызовы), а второй уровень получает свой ключ
  (`depth`/`multipv` не смешиваются);
* выключенный кэш не пишет строк и всегда идёт в движок;
* устаревшая запись (старше TTL) — промах, `cleanup_expired` удаляет
  только старые строки;
* сквозной `POST /v1/analyze` на реальном Stockfish: повторный анализ того
  же PGN быстрее и не плодит новые строки кэша (DoD итерации 30).

Требуется PostgreSQL (модуль) и Stockfish (последний тест); без них
соответствующие тесты скипаются.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import EngineCacheEntry
from app.engine.cache import EngineCache, EngineCacheKey, engine_fen_hash
from app.engine.models import EngineLine
from app.engine.service import EngineService
from app.main import create_app

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
PGN = (
    '[Event "Cache"]\n[White "White"]\n[Black "Black"]\n'
    '[Result "1/2-1/2"]\n\n1. e4 e5 2. Nf3 Nc6 1/2-1/2'
)

CLUB_LINES = [
    EngineLine(multipv=1, depth=18, score_cp=24, move_uci="g1f3", pv=["g1f3"]),
    EngineLine(multipv=2, depth=18, score_cp=18, move_uci="d2d4", pv=["d2d4"]),
    EngineLine(multipv=3, depth=18, score_cp=12, move_uci="c2c4", pv=["c2c4"]),
]


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


def _stockfish_available() -> bool:
    return Path(get_settings().stockfish_path).is_file()


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _dispose_session_engine() -> None:
    """Закрыть общий пул `app.db.session`: тесты крутят много event loop-ов.

    `EngineCache` использует глобальный `SessionLocal`; соединения asyncpg
    привязаны к loop, и без dispose следующий `asyncio.run` получает
    «Event loop is closed» при переиспользовании пула.
    """
    from app.db.session import engine as session_engine

    await session_engine.dispose()


async def _clear_cache() -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(delete(EngineCacheEntry))
    finally:
        await engine.dispose()


async def _cache_rows() -> list[EngineCacheEntry]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = await session.scalars(
                select(EngineCacheEntry).order_by(EngineCacheEntry.id)
            )
            return list(rows)
    finally:
        await engine.dispose()


async def _cache_count() -> int:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with async_sessionmaker(
            engine, expire_on_commit=False
        )() as session:
            return int(
                await session.scalar(
                    select(func.count()).select_from(EngineCacheEntry)
                )
                or 0
            )
    finally:
        await engine.dispose()


async def _insert_row(*, created_at: datetime, depth: int = 18) -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                EngineCacheEntry(
                    fen_hash=engine_fen_hash(START_FEN),
                    engine_version="Stockfish test-17",
                    depth=depth,
                    multipv=3 if depth == 18 else 1,
                    lines=[line.model_dump(mode="json") for line in CLUB_LINES],
                    created_at=created_at,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


class _FakeUciEngine:
    """Подмена UCI-процесса: считает вызовы `go` и отдаёт готовые линии."""

    def __init__(self, lines: list[EngineLine]) -> None:
        self.name = "Stockfish test-17"
        self.is_running = True
        self.calls: list[dict[str, Any]] = []
        self._lines = lines

    async def go(self, **kwargs: Any) -> list[EngineLine]:
        self.calls.append(kwargs)
        return list(self._lines)

    async def close(self) -> None:
        """Заглушка закрытия процесса: fake ничего не запускает."""
        self.is_running = False


def _service_with_fake(
    *, cache: EngineCache | None, lines: list[EngineLine] | None = None
) -> tuple[EngineService, _FakeUciEngine]:
    service = EngineService(
        get_settings(), executable=Path("unused-stockfish"), cache=cache
    )
    fake = _FakeUciEngine(list(lines or CLUB_LINES))
    service._engine = fake  # type: ignore[assignment]
    return service, fake


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


def test_second_analyze_hits_cache() -> None:
    async def scenario() -> None:
        await _clear_cache()
        cache = EngineCache(get_settings())
        service, fake = _service_with_fake(cache=cache)
        try:
            first = await service.analyze(START_FEN, "club")
            second = await service.analyze(START_FEN, "club")
            assert first == second
            assert len(fake.calls) == 1, "второй анализ должен взять кэш"
            rows = await _cache_rows()
            assert len(rows) == 1
            row = rows[0]
            assert row.engine_version == "Stockfish test-17"
            assert row.depth == 18
            assert row.multipv == 3
            assert row.fen_hash == engine_fen_hash(START_FEN)
        finally:
            await service.aclose()
            await _clear_cache()
            await _dispose_session_engine()

    asyncio.run(scenario())


def test_cache_separates_levels_by_depth_and_multipv() -> None:
    async def scenario() -> None:
        await _clear_cache()
        cache = EngineCache(get_settings())
        service, fake = _service_with_fake(cache=cache)
        try:
            await service.analyze(START_FEN, "club")
            await service.analyze(START_FEN, "gm")
            assert len(fake.calls) == 2, "gm не должен получить кэш club"
            rows = await _cache_rows()
            assert {(row.depth, row.multipv) for row in rows} == {
                (18, 3),
                (28, 5),
            }
        finally:
            await service.aclose()
            await _clear_cache()
            await _dispose_session_engine()

    asyncio.run(scenario())


def test_disabled_cache_always_calls_engine() -> None:
    async def scenario() -> None:
        await _clear_cache()
        service, fake = _service_with_fake(cache=None)
        try:
            await service.analyze(START_FEN, "club")
            await service.analyze(START_FEN, "club")
            assert len(fake.calls) == 2
            assert await _cache_count() == 0
        finally:
            await service.aclose()
            await _clear_cache()
            await _dispose_session_engine()

    asyncio.run(scenario())


def test_expired_entry_is_a_miss() -> None:
    async def scenario() -> None:
        await _clear_cache()
        settings = get_settings()
        cache = EngineCache(settings)
        old = datetime.now(UTC) - timedelta(days=settings.engine_cache_ttl_days + 1)
        await _insert_row(created_at=old)
        key = EngineCacheKey(
            fen_hash=engine_fen_hash(START_FEN),
            engine_version="Stockfish test-17",
            depth=18,
            multipv=3,
        )
        assert await cache.lookup(key) is None
        service, fake = _service_with_fake(cache=cache)
        try:
            await service.analyze(START_FEN, "club")
            assert len(fake.calls) == 1
            # Перезапись старой строки: upsert обновляет created_at, новых нет.
            assert await _cache_count() == 1
        finally:
            await service.aclose()
            await _clear_cache()
            await _dispose_session_engine()

    asyncio.run(scenario())


def test_cleanup_expired_removes_only_old_rows() -> None:
    async def scenario() -> None:
        await _clear_cache()
        settings = get_settings()
        cache = EngineCache(settings)
        old = datetime.now(UTC) - timedelta(days=settings.engine_cache_ttl_days + 1)
        fresh = datetime.now(UTC) - timedelta(days=1)
        await _insert_row(created_at=old, depth=18)
        await _insert_row(created_at=fresh, depth=28)
        assert await cache.cleanup_expired() == 1
        assert await _cache_count() == 1
        await _clear_cache()
        await _dispose_session_engine()

    asyncio.run(scenario())


# --- Сквозной сценарий с реальным Stockfish -------------------------------


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
            "engine_cache_enabled": True,
            "engine_pool_size": 2,
        }
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as test_client:
        yield test_client


def test_repeated_analyze_uses_cache_and_is_faster(
    client: TestClient,
) -> None:
    if not _stockfish_available():
        pytest.skip("Stockfish не найден (KI-006)")

    async def clear() -> None:
        await _clear_cache()

    async def count_rows() -> int:
        return await _cache_count()

    asyncio.run(clear())
    try:
        first = client.post(
            "/v1/analyze", json={"pgn": PGN, "level": "beginner"}
        )
        assert first.status_code == 200, first.text
        first_ms = int(first.json()["time_taken_ms"])

        second = client.post(
            "/v1/analyze", json={"pgn": PGN, "level": "beginner"}
        )
        assert second.status_code == 200, second.text
        second_ms = int(second.json()["time_taken_ms"])
        assert second.json()["moves_analyzed"] == first.json()["moves_analyzed"]

        # Кэш должен быть заполнен по числу позиций (полуходы + старт).
        rows = asyncio.run(count_rows())
        assert rows == int(first.json()["moves_analyzed"]) + 1

        # DoD: повторный анализ заметно быстрее (без поиска Stockfish).
        assert second_ms < first_ms, (first_ms, second_ms)
        assert second_ms < first_ms * 0.5, (first_ms, second_ms)
    finally:
        asyncio.run(clear())
