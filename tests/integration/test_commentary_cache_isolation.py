"""KI-034/T-032b: две партии с одинаковым FEN не обмениваются комментариями."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.commentary.service import generate_commentary
from app.config import Settings, get_settings
from app.db.models import Game, Move, SemanticCache
from app.ingestion import parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

# Транспозиция: разные первые ходы, идентичная итоговая позиция.
PGN_A = "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *"
PGN_B = "1. Nf3 Nc6 2. e4 e5 3. Bb5 a6 *"


class OrderAwareLLM(BaseLLMClient):
    """Ответ зависит от порядка ходов в пакете фактов."""

    def __init__(self) -> None:
        super().__init__(model="order-aware-model")
        self.calls = 0

    @property
    def provider(self) -> str:
        return "test"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.calls += 1
        marker = "A" if "1. e4" in prompt else "B"
        return LLMResult(
            text=f"Текст партии {marker}.",
            usage=LLMUsage(
                provider="test",
                model="order-aware-model",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.0,
            ),
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


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


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
            await db_session.execute(text("DELETE FROM semantic_cache"))
            await db_session.commit()
    await engine.dispose()


def _settings() -> Settings:
    return Settings(
        embedding_provider="hash",
        semantic_cache_enabled=True,
        semantic_cache_ttl_days=30,
        semantic_cache_similarity_threshold=0.05,
    )


async def _seed(db_session: AsyncSession, pgn: str) -> Game:
    parsed = parse_pgn(pgn)
    game = Game(pgn=pgn, eco_code=parsed.eco_code)
    db_session.add(game)
    await db_session.flush()
    for move in parsed.moves:
        db_session.add(
            Move(
                game_id=game.id,
                ply=move.ply,
                san=move.san,
                fen=move.fen,
                eval_cp=20,
                delta_cp=0,
                classification="good",
                engine_depth=12,
            )
        )
    await db_session.flush()
    return game


async def test_same_final_position_different_history_is_cache_miss(
    session: AsyncSession,
) -> None:
    """Партии A и B приходят в один FEN, но не переиспользуют текст друг друга."""
    cfg = _settings()
    game_a = await _seed(session, PGN_A)
    game_b = await _seed(session, PGN_B)
    assert parse_pgn(PGN_A).final_fen == parse_pgn(PGN_B).final_fen

    llm = OrderAwareLLM()
    first = await generate_commentary(
        session, game_a.id, "club", client=llm, settings=cfg
    )
    second = await generate_commentary(
        session, game_b.id, "club", client=llm, settings=cfg
    )

    assert first.cached is False
    assert second.cached is False
    assert llm.calls == 2
    assert "партии A" in first.text
    assert "партии B" in second.text


async def test_use_cache_false_bypasses_read_and_write(
    session: AsyncSession,
) -> None:
    """`use_cache=False` не читает и не пишет кэш (T-032b)."""
    cfg = _settings()
    game = await _seed(session, PGN_A)
    llm = OrderAwareLLM()

    first = await generate_commentary(
        session, game.id, "club", client=llm, settings=cfg, use_cache=False
    )
    second = await generate_commentary(
        session, game.id, "club", client=llm, settings=cfg, use_cache=False
    )

    assert first.cached is False
    assert second.cached is False
    assert llm.calls == 2
    rows = (await session.scalars(select(SemanticCache))).all()
    assert list(rows) == []
