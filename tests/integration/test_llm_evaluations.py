"""Сохранение LLM-as-judge в `llm_evaluations` (T-039)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import AnnotatedGame, LlmEvaluation
from scripts.evaluate_llm import (
    CommentaryRow,
    GameRow,
    _load_games,
    _save_evaluations,
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


def test_save_evaluations_inserts_rows() -> None:
    game = GameRow(
        game_id=1,
        pgn="1. e4 e5",
        white="White",
        black="Black",
        event="Test",
    )
    row = CommentaryRow(
        game=game,
        level="club",
        model="test-eval-model",
        scores={
            "factual_accuracy": 7.0,
            "usefulness": 8.0,
            "style": 9.0,
        },
    )
    settings = get_settings()

    async def run() -> list[LlmEvaluation]:
        saved = await _save_evaluations([row], settings)
        assert saved == 1
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                rows = (
                    await session.scalars(
                        select(LlmEvaluation)
                        .where(LlmEvaluation.model == "test-eval-model")
                        .order_by(LlmEvaluation.id.desc())
                        .limit(1)
                    )
                ).all()
                return list(rows)
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(LlmEvaluation).where(
                        LlmEvaluation.model == "test-eval-model"
                    )
                )
        finally:
            await engine.dispose()

    try:
        rows = asyncio.run(run())
        assert len(rows) == 1
        assert rows[0].level.value == "club"
        assert rows[0].accuracy_score == 7.0
        assert rows[0].usefulness_score == 8.0
        assert rows[0].style_score == 9.0
    finally:
        asyncio.run(cleanup())


def test_load_games_filters_by_source() -> None:
    """T-042: `--source` фиксирует eval-набор (new_samples/my-collection)."""
    settings = get_settings()
    prefix = "test-eval-source-"

    async def seed() -> None:
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add_all(
                    [
                        AnnotatedGame(source=f"{prefix}a", pgn="1. e4 e5"),
                        AnnotatedGame(source=f"{prefix}a", pgn="1. d4 d5 2. c4"),
                        AnnotatedGame(source=f"{prefix}b", pgn="1. e4 c5"),
                    ]
                )
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(
            settings.database_url, poolclass=NullPool
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(AnnotatedGame).where(
                        AnnotatedGame.source.like(f"{prefix}%")
                    )
                )
        finally:
            await engine.dispose()

    async def load(source: str) -> list[GameRow]:
        return await _load_games(10, settings, source=source)

    try:
        asyncio.run(seed())

        first = asyncio.run(load(f"{prefix}a"))
        assert len(first) == 2
        assert all("e4 e5" in game.pgn or "c4" in game.pgn for game in first)

        second = asyncio.run(load(f"{prefix}b"))
        assert [game.pgn for game in second] == ["1. e4 c5"]

        assert asyncio.run(load("no-such-source")) == []
    finally:
        asyncio.run(cleanup())
