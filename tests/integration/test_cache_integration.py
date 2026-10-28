"""Интеграция кэша с сервисом комментария (T-032)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cache import chat_position_fen, fen_hash
from app.chat.service import answer_question
from app.commentary.service import generate_commentary
from app.commentary.streaming import stream_commentary
from app.config import Settings, get_settings
from app.db.models import Game, Move, SemanticCache
from app.ingestion import parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage

PGN = "1. d4 Nf6 2. c4 e6 1/2-1/2"
TEXT = "Партия началась с d4, затем Nf6."


class CountingLLM(BaseLLMClient):
    """Считает вызовы; второй запрос при кэше не должен сюда попасть."""

    def __init__(self) -> None:
        super().__init__(model="counting-model")
        self.calls = 0

    @property
    def provider(self) -> str:
        return "counting"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.calls += 1
        return LLMResult(
            text=TEXT,
            usage=LLMUsage(
                provider="counting",
                model="counting-model",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.0,
            ),
        )


class StreamingLLM(CountingLLM):
    """Fake с потоковой отдачей текста."""

    def __init__(self) -> None:
        super().__init__()
        self.stream_calls = 0

    async def stream_generate(
        self, prompt: str, system_prompt: str | None = None
    ) -> AsyncIterator[str]:
        self.stream_calls += 1
        yield TEXT


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


def _settings() -> Settings:
    return Settings(
        embedding_provider="hash",
        semantic_cache_enabled=True,
        semantic_cache_ttl_days=30,
        semantic_cache_similarity_threshold=0.05,
    )


async def _seed(db_session: AsyncSession) -> Game:
    parsed = parse_pgn(PGN)
    game = Game(pgn=PGN, eco_code=parsed.eco_code)
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


async def test_second_commentary_is_cache_hit(
    session: AsyncSession,
) -> None:
    cfg = _settings()
    game = await _seed(session)
    llm = CountingLLM()

    first = await generate_commentary(
        session, game.id, "club", client=llm, settings=cfg
    )
    second = await generate_commentary(
        session, game.id, "club", client=llm, settings=cfg
    )

    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text
    assert second.usage.model == "semantic-cache"
    assert second.usage.cost_usd == 0.0
    assert llm.calls == 1


def _events(events: list[dict[str, str]]) -> list[tuple[str, dict[str, object]]]:
    return [
        (event["event"], json.loads(event["data"])) for event in events
    ]


async def test_streaming_commentary_cache_hit() -> None:
    cfg = _settings()
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    parsed = parse_pgn(PGN)
    game_id = 0
    try:
        async with factory() as seed_session:
            game = await _seed(seed_session)
            await seed_session.commit()
            game_id = game.id
        llm = StreamingLLM()
        async with factory() as stream_session:
            first_events = [
                event
                async for event in stream_commentary(
                    stream_session,
                    game_id,
                    "club",
                    settings=cfg,
                    client=llm,
                )
            ]
        async with factory() as stream_session:
            second_events = [
                event
                async for event in stream_commentary(
                    stream_session,
                    game_id,
                    "club",
                    settings=cfg,
                    client=llm,
                )
            ]

        first = _events(first_events)
        second = _events(second_events)
        assert not [event for event, _ in first if event == "error"]
        assert not [event for event, _ in second if event == "error"]
        second_meta = next(payload for event, payload in second if event == "meta")
        second_done = next(payload for event, payload in second if event == "done")
        assert second_meta["model"] == "semantic-cache"
        assert second_done["tokens_used"] == 0
        assert llm.stream_calls == 1
    finally:
        async with factory() as cleanup_session:
            await cleanup_session.execute(
                delete(SemanticCache).where(
                    SemanticCache.fen_hash == fen_hash(parsed.initial_fen)
                )
            )
            await cleanup_session.execute(
                delete(Game).where(Game.id == game_id)
            )
            await cleanup_session.commit()
        await engine.dispose()


async def test_second_chat_question_is_cache_hit() -> None:
    cfg = _settings()
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    parsed = parse_pgn(PGN)
    position_fen = chat_position_fen(parsed)
    game_id = 0
    try:
        async with factory() as seed_session:
            game = await _seed(seed_session)
            await seed_session.commit()
            game_id = game.id
        llm = CountingLLM()
        async with factory() as chat_session:
            first = await answer_question(
                chat_session,
                game_id,
                "club",
                "Кто играл белыми?",
                client=llm,
                settings=cfg,
            )
            second = await answer_question(
                chat_session,
                game_id,
                "club",
                "Кто играл белыми?",
                client=llm,
                settings=cfg,
            )

        assert first.cached is False
        assert second.cached is True
        assert second.usage.model == "semantic-cache"
        assert llm.calls == 1
    finally:
        async with factory() as cleanup_session:
            await cleanup_session.execute(
                delete(SemanticCache).where(
                    SemanticCache.fen_hash == fen_hash(position_fen)
                )
            )
            await cleanup_session.execute(
                delete(Game).where(Game.id == game_id)
            )
            await cleanup_session.commit()
        await engine.dispose()
