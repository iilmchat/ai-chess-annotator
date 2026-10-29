"""Интеграционные тесты retention `llm_calls` (KI-048) на живой БД.

Проверяем батчевый DELETE:

* старые строки (created_at < cutoff) удаляются, свежие остаются;
* удаление идёт батчами по `--batch-size` и коммитит каждый батч;
* `--max-batches` ограничивает работу за запуск (capped=True);
* `--dry-run` только считает строки;
* CLI `main([...])` печатает JSON-отчёт и возвращает exit code 0.

Тестовые строки помечены префиксом `span_id = "prune-…"` и удаляются после
теста. Требуется PostgreSQL; без него модуль скипается.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import LlmCall
from scripts.prune_llm_calls import main as prune_main
from scripts.prune_llm_calls import prune_batches

#: Дни, старше которых строки считаются «старыми» во всех тестах.
RETENTION_DAYS = 90

#: Префикс span_id тестовых строк — для точечной уборки.
SPAN_PREFIX = "prune-"


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


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(*, old: int, recent: int) -> None:
    """Добавить `old` строк старше 90 дней и `recent` свежих."""
    now = datetime.now(UTC)
    factory = _factory()
    async with factory() as session:
        for index in range(old):
            session.add(
                LlmCall(
                    trace_id=uuid4(),
                    span_id=f"{SPAN_PREFIX}old-{index}-{uuid4().hex[:8]}",
                    operation="chat",
                    provider="test",
                    model="prune-test-model",
                    latency_ms=10,
                    status="ok",
                    created_at=now - timedelta(days=400 + index),
                )
            )
        for index in range(recent):
            session.add(
                LlmCall(
                    trace_id=uuid4(),
                    span_id=f"{SPAN_PREFIX}new-{index}-{uuid4().hex[:8]}",
                    operation="chat",
                    provider="test",
                    model="prune-test-model",
                    latency_ms=10,
                    status="ok",
                    created_at=now - timedelta(days=index),
                )
            )
        await session.commit()


async def _cleanup() -> None:
    """Удалить все строки, созданные этими тестами."""
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(LlmCall).where(LlmCall.span_id.like(f"{SPAN_PREFIX}%"))
            )
    finally:
        await engine.dispose()


async def _count_seeded(*, old_only: bool) -> int:
    """Сколько тестовых строк осталось (опционально — только старых)."""
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with async_sessionmaker(
            engine, expire_on_commit=False
        )() as session:
            statement = select(func.count()).select_from(LlmCall).where(
                LlmCall.span_id.like(f"{SPAN_PREFIX}%")
            )
            if old_only:
                statement = statement.where(LlmCall.created_at < cutoff)
            return int(await session.scalar(statement) or 0)
    finally:
        await engine.dispose()


def test_prune_deletes_only_old_rows_in_batches() -> None:
    async def scenario() -> None:
        await _cleanup()
        await _seed(old=5, recent=3)
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                stats = await prune_batches(
                    session,
                    retention_days=RETENTION_DAYS,
                    batch_size=2,
                )
        finally:
            await engine.dispose()
        assert stats.deleted == 5
        # 5 строк по 2 → 2+2+1: минимум три транзакции.
        assert stats.batches >= 3
        assert stats.capped is False
        assert await _count_seeded(old_only=True) == 0
        assert await _count_seeded(old_only=False) == 3

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_max_batches_caps_run() -> None:
    async def scenario() -> None:
        await _cleanup()
        await _seed(old=5, recent=1)
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                stats = await prune_batches(
                    session,
                    retention_days=RETENTION_DAYS,
                    batch_size=2,
                    max_batches=1,
                )
        finally:
            await engine.dispose()
        assert stats.deleted == 2
        assert stats.batches == 1
        assert stats.capped is True
        # Остались 3 старые строки — их добьёт следующий запуск.
        assert await _count_seeded(old_only=True) == 3

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_prune_dry_run_counts_without_deleting() -> None:
    async def scenario() -> None:
        await _cleanup()
        await _seed(old=3, recent=1)
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                stats = await prune_batches(
                    session,
                    retention_days=RETENTION_DAYS,
                    dry_run=True,
                )
        finally:
            await engine.dispose()
        assert stats.dry_run is True
        assert stats.deleted == 3
        assert stats.batches == 0
        assert await _count_seeded(old_only=True) == 3

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_cli_main_prints_json_and_deletes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def seed() -> None:
        await _cleanup()
        await _seed(old=2, recent=1)

    async def remaining_old() -> int:
        return await _count_seeded(old_only=True)

    try:
        asyncio.run(seed())
        code = prune_main(
            [
                "--retention-days",
                str(RETENTION_DAYS),
                "--batch-size",
                "100",
                "--json",
            ]
        )
        assert code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["retention_days"] == RETENTION_DAYS
        assert report["deleted"] >= 2
        assert report["dry_run"] is False
        assert asyncio.run(remaining_old()) == 0
    finally:
        asyncio.run(_cleanup())
