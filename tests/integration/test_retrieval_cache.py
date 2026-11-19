"""T-018: retrieval_cache — miss → запись top_k_ids → hit без эмбеддера."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.engine.models import MoveAnalysis
from app.ingestion import parse_pgn
from app.rag.cache import RetrievalCache, retrieval_fen_hash
from app.rag.context import make_rag_context_builder
from app.rag.embedder import Embedder, HashEmbedder
from app.rag.service import find_similar_positions, ingest_pgn_text

PGN = """[Event "Cache Test"]
[Date "2024.01.01"]

1. e4 {Центр.} e5 2. Nf3 {Развитие коня.} Nc6 *
"""
FEN = parse_pgn(PGN).final_fen


class CountingEmbedder:
    """Считает обращения к embedding-модели."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner
        self.calls = 0

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    async def embed(self, text_value: str) -> list[float]:
        self.calls += 1
        return await self._inner.embed(text_value)


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


@pytest.fixture()
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture()
async def session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.execute(
                text("DELETE FROM retrieval_cache WHERE fen_hash = :h"),
                {"h": retrieval_fen_hash(FEN)},
            )
            await db_session.execute(
                text("DELETE FROM annotated_games WHERE source = 'retrieval-cache-test'")
            )
            await db_session.commit()


def _settings() -> Settings:
    return Settings(
        embedding_provider="hash",
        retrieval_cache_enabled=True,
        retrieval_cache_ttl_days=30,
        rag_club_enabled=True,
        rag_club_top_k=1,
    )


async def _seed_annotated(session: AsyncSession) -> list[int]:
    ids = await ingest_pgn_text(
        session, PGN, embedder=HashEmbedder(), source="retrieval-cache-test"
    )
    await session.commit()
    return ids


async def test_cache_miss_stores_ids_then_hit_skips_embedder(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    cfg = _settings()
    await _seed_annotated(session)
    cache = RetrievalCache(cfg, session_factory=factory)
    assert await cache.lookup(FEN) is None

    spy = CountingEmbedder(HashEmbedder())
    first = await find_similar_positions(
        FEN, top_k=2, session=session, cache=cache, embedder=spy
    )
    assert first
    assert spy.calls == 1

    stored = await cache.lookup(FEN)
    assert stored is not None
    assert len(stored) == 2
    assert all(item > 0 for item in stored)

    spy.calls = 0
    second = await find_similar_positions(
        FEN, top_k=2, session=session, cache=cache, embedder=spy
    )
    assert second == first
    assert spy.calls == 0


async def test_expired_entry_is_ignored(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    cfg = Settings(
        embedding_provider="hash",
        retrieval_cache_enabled=True,
        retrieval_cache_ttl_days=30,
    )
    await _seed_annotated(session)
    cache = RetrievalCache(cfg, session_factory=factory)
    await cache.store(FEN, [1, 2])

    await session.execute(
        text(
            "UPDATE retrieval_cache "
            "SET created_at = now() - interval '31 days' "
            "WHERE fen_hash = :h"
        ),
        {"h": retrieval_fen_hash(FEN)},
    )
    await session.commit()

    assert await cache.lookup(FEN) is None


async def test_cleanup_expired_removes_only_old_rows(
    session: AsyncSession, factory: async_sessionmaker[AsyncSession]
) -> None:
    cfg = _settings()
    cache = RetrievalCache(cfg, session_factory=factory)
    await cache.store(FEN, [1])
    await session.execute(
        text(
            "UPDATE retrieval_cache "
            "SET created_at = now() - interval '31 days' "
            "WHERE fen_hash = :h"
        ),
        {"h": retrieval_fen_hash(FEN)},
    )
    await session.commit()

    assert await cache.cleanup_expired() >= 1
    assert await cache.lookup(FEN) is None


async def test_club_retrieval_uses_reduced_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPEC §3: club — уменьшенный top_k и только при RAG_CLUB_ENABLED."""
    seen: list[int] = []

    async def fake_find(
        fen: str, top_k: int = 3, **_: object
    ) -> list[str]:
        seen.append(top_k)
        return ["Похожая аннотация"]

    monkeypatch.setattr("app.rag.context.find_similar_positions", fake_find)

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> bool:
            return False

    cfg = _settings()
    builder = make_rag_context_builder(
        cfg, session_factory=lambda: _Session()  # type: ignore[arg-type,return-value]
    )
    parsed = parse_pgn("1. d4 Nf6 2. c4 e6 1/2-1/2")
    analysis = MoveAnalysis(
        ply=1,
        played_move_san="d4",
        played_move_uci="d2d4",
        eval_before=10,
        eval_after=0,
        best_move_uci="d2d4",
        best_move_san="d4",
        delta_cp=130,
        classification="mistake",
        lines=[],
    )

    club = await builder(parsed, [analysis], "club")
    assert club is not None
    assert seen == [1]

    gm = await builder(parsed, [analysis], "gm")
    assert gm is not None
    assert seen == [1, 3]

    beginner = await builder(parsed, [analysis], "beginner")
    assert beginner is None
    assert seen == [1, 3]


async def test_club_retrieval_disabled_by_default() -> None:
    cfg = Settings(embedding_provider="hash", rag_club_enabled=False)
    builder = make_rag_context_builder(cfg)
    parsed = parse_pgn("1. d4 Nf6 2. c4 e6 1/2-1/2")
    analysis = MoveAnalysis(
        ply=1,
        played_move_san="d4",
        played_move_uci="d2d4",
        eval_before=10,
        eval_after=0,
        best_move_uci="d2d4",
        best_move_san="d4",
        delta_cp=130,
        classification="mistake",
        lines=[],
    )

    assert await builder(parsed, [analysis], "club") is None
