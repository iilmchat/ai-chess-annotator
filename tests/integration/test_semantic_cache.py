"""Semantic cache: exact/semantic/TTL/UPSERT (T-032)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cache.semantic import (
    cleanup_expired_cache,
    lookup,
    store,
)
from app.config import Settings, get_settings
from app.db.models import Level, SemanticCache

FEN_A = "8/8/8/8/8/8/8/K6k w - - 0 1"
FEN_B = "8/8/8/8/8/8/7P/K6k w - - 0 1"
VECTOR = [1.0] + [0.0] * 767


class FixedEmbedder:
    """Возвращает один и тот же L2-вектор для любого текста."""

    @property
    def dimension(self) -> int:
        return 768

    async def embed(self, _text: str) -> list[float]:
        return list(VECTOR)


def _settings() -> Settings:
    return Settings(
        semantic_cache_enabled=True,
        semantic_cache_ttl_days=30,
        semantic_cache_similarity_threshold=0.05,
        embedding_provider="hash",
    )


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


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


async def test_exact_hit(session: AsyncSession) -> None:
    cfg = _settings()
    emb = FixedEmbedder()

    assert (
        await store(
            session,
            fen=FEN_A,
            level="club",
            prompt_hash_value="p1",
            response_text="точный ответ",
            settings=cfg,
            embedder=emb,
        )
        is True
    )
    hit = await lookup(
        session,
        fen=FEN_A,
        level="club",
        prompt_hash_value="p1",
        settings=cfg,
        embedder=emb,
    )

    assert hit is not None
    assert hit.kind == "exact"
    assert hit.text == "точный ответ"


async def test_semantic_hit_for_near_position(session: AsyncSession) -> None:
    cfg = _settings()
    emb = FixedEmbedder()
    await store(
        session,
        fen=FEN_A,
        level="club",
        prompt_hash_value="p1",
        response_text="семантический ответ",
        settings=cfg,
        embedder=emb,
    )

    hit = await lookup(
        session,
        fen=FEN_B,
        level="club",
        prompt_hash_value="p1",
        settings=cfg,
        embedder=emb,
    )

    assert hit is not None
    assert hit.kind == "semantic"
    assert hit.distance == pytest.approx(0.0, abs=1e-9)


async def test_other_prompt_or_level_misses(session: AsyncSession) -> None:
    cfg = _settings()
    emb = FixedEmbedder()
    await store(
        session,
        fen=FEN_A,
        level="club",
        prompt_hash_value="p1",
        response_text="ответ",
        settings=cfg,
        embedder=emb,
    )

    assert (
        await lookup(
            session,
            fen=FEN_A,
            level="club",
            prompt_hash_value="other",
            settings=cfg,
            embedder=emb,
        )
        is None
    )
    assert (
        await lookup(
            session,
            fen=FEN_A,
            level="gm",
            prompt_hash_value="p1",
            settings=cfg,
            embedder=emb,
        )
        is None
    )


async def test_store_upserts_same_key(session: AsyncSession) -> None:
    cfg = _settings()
    emb = FixedEmbedder()
    for response in ("старый", "новый"):
        await store(
            session,
            fen=FEN_A,
            level="club",
            prompt_hash_value="p1",
            response_text=response,
            settings=cfg,
            embedder=emb,
        )

    hit = await lookup(
        session,
        fen=FEN_A,
        level="club",
        prompt_hash_value="p1",
        settings=cfg,
        embedder=emb,
    )

    assert hit is not None
    assert hit.text == "новый"


async def test_expired_entry_is_ignored_and_cleaned(
    session: AsyncSession,
) -> None:
    cfg = _settings()
    old = SemanticCache(
        fen_hash="a" * 64,
        fen_embedding=VECTOR,
        level=Level.club,
        prompt_hash="old",
        response_text="старый",
        created_at=datetime.now(UTC) - timedelta(days=31),
    )
    session.add(old)
    await session.flush()

    assert (
        await lookup(
            session,
            fen=FEN_A,
            level="club",
            prompt_hash_value="old",
            settings=cfg,
        )
        is None
    )
    deleted = await cleanup_expired_cache(session, settings=cfg)

    assert deleted >= 1
