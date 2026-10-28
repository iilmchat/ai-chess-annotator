"""Промпты из БД и инвалидация кэша (T-029, итерация 17).

Проверяем главное обещание: правка строки `prompts` + `reset_prompt_cache`
меняет текст, который увидит следующий LLM-вызов, без рестарта.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Prompt
from app.prompts import (
    load_system_prompt,
    reset_prompt_cache,
    seed_prompts,
)

MARKER = "\n\nТЕСТ ИТЕРАЦИИ 17: этот текст должен попасть в кэш."


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


async def test_prompt_update_and_cache_reset_return_new_text() -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await seed_prompts(session)
            row = await session.scalar(
                select(Prompt).where(Prompt.level == "club")
            )
            assert row is not None
            original = row.content
            try:
                row.content = original + MARKER
                row.version = (row.version or 1) + 1
                await session.commit()

                reset_prompt_cache("club")
                updated = await load_system_prompt("club")

                assert updated.endswith(MARKER.strip())
                assert updated != original.strip()
            finally:
                row.content = original
                row.version = max(1, (row.version or 2) - 1)
                await session.commit()
                reset_prompt_cache("club")
                restored = await load_system_prompt("club")
                assert restored == original.strip()
    finally:
        await engine.dispose()


async def test_seed_prompts_is_idempotent() -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await seed_prompts(session)
            first = (await session.scalars(select(Prompt.level))).all()
            added = await seed_prompts(session)
            second = (await session.scalars(select(Prompt.level))).all()

        assert added == 0
        assert sorted(first) == sorted(second)
        assert {"beginner", "club", "gm"} <= set(second)
    finally:
        await engine.dispose()
