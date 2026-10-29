"""Живые тесты реальных эмбеддингов LM Studio (KI-025) + e2e ingest/retrieval.

Сюита по умолчанию офлайн: `tests/conftest.py` выставляет
`EMBEDDING_PROVIDER=hash`. Здесь настройки/эмбеддер задаются **явно**
(`settings.model_copy(update={"embedding_provider": "lmstudio"})`), поэтому
тесты реально ходят в LM Studio (`VLLM_BASE_URL`, по умолчанию
`http://127.0.0.1:8034/v1`) и пишут в PostgreSQL (порт задаётся
`POSTGRES_PORT`; временная БД команды — 5435).

Модуль целиком пропускается, если LM Studio (embedding-модель в
`GET /v1/models`) или PostgreSQL недоступны, — CI без сервисов остаётся
зелёным.

Вставленные строки `annotated_games` удаляются по `id` в `finally`.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from pathlib import Path

import chess
import chess.pgn
import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import AnnotatedGame
from app.rag.embedder import LMStudioEmbedder, get_embedder, position_document
from app.rag.service import (
    find_similar_positions,
    ingest_pgn_text,
    parse_pgn_games,
)

EXPECTED_DIM = 768
LIVE_SOURCE = "emb-live-test"
EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"

SAMPLE_PGN = (
    Path(__file__).resolve().parents[1] / "fixtures" / "rag_sample.pgn"
).read_text(encoding="utf-8")

START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
OTHER_FEN = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"


def _live_settings() -> Settings:
    """Настройки реального провайдера (перекрывает `hash` из conftest)."""
    return get_settings().model_copy(update={"embedding_provider": "lmstudio"})


def _lmstudio_available() -> bool:
    """LM Studio отвечает и нужная embedding-модель загружена."""
    settings = _live_settings()
    try:
        response = httpx.get(f"{settings.vllm_base_url}/models", timeout=2.0)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
        models = {item["id"] for item in payload["data"]}
    except (KeyError, TypeError, ValueError):
        return False
    return settings.embedding_model in models


def _db_available() -> bool:
    async def ping() -> None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
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


pytestmark = [
    pytest.mark.skipif(
        not _lmstudio_available(),
        reason="LM Studio с embedding-моделью недоступен",
    ),
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db_session:
        try:
            yield db_session
        finally:
            await db_session.rollback()
    await engine.dispose()


def _final_fen(game: chess.pgn.Game) -> str:
    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    return board.fen()


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


# --- живой провайдер ------------------------------------------------------


async def test_live_provider_is_lmstudio_with_768d_normalized_vectors() -> None:
    settings = _live_settings()
    embedder = get_embedder(settings)

    assert isinstance(embedder, LMStudioEmbedder)
    assert embedder.dimension == EXPECTED_DIM
    assert settings.embedding_model == EMBEDDING_MODEL

    vector = await embedder.embed(position_document(START_FEN))

    assert len(vector) == EXPECTED_DIM
    assert _norm(vector) == pytest.approx(1.0, abs=1e-6)
    # Реальная dense-модель: большинство координат ненулевые (у хеш-заглушки
    # их единицы), значит это не HashEmbedder.
    assert sum(1 for value in vector if value != 0.0) > 100
    print(
        f"\nlive provider={type(embedder).__name__} model={embedder.model} "
        f"dim={len(vector)} norm={_norm(vector):.9f}"
    )


async def test_live_same_text_is_stable() -> None:
    embedder = get_embedder(_live_settings())
    document = position_document(START_FEN)

    first = await embedder.embed(document)
    second = await embedder.embed(document)

    assert _cosine(first, second) > 0.999
    print(f"\nlive stability cosine={_cosine(first, second):.9f}")


async def test_live_embed_many_returns_two_768d_vectors() -> None:
    settings = _live_settings()
    embedder = get_embedder(settings)
    documents = [position_document(START_FEN), position_document(OTHER_FEN)]

    batched = await embedder.embed_many(documents)
    assert len(batched) == 2
    assert all(len(vector) == EXPECTED_DIM for vector in batched)
    assert all(_norm(vector) == pytest.approx(1.0, abs=1e-6) for vector in batched)

    # batch_size=1 — два HTTP-запроса (проверяем чанкование на живом сервере).
    chunked_embedder = LMStudioEmbedder(
        base_url=settings.vllm_base_url,
        model=settings.embedding_model,
        dimension=EXPECTED_DIM,
        api_key=settings.vllm_api_key,
        timeout_s=settings.llm_timeout_s,
        batch_size=1,
    )
    chunked = await chunked_embedder.embed_many(documents)

    assert len(chunked) == 2
    assert _cosine(batched[0], chunked[0]) > 0.999
    print(
        f"\nlive batch sizes: {[len(v) for v in batched]} "
        f"cos(batched,chunked)={_cosine(batched[0], chunked[0]):.9f}"
    )


# --- e2e: ingest реальным эмбеддером + retrieval -------------------------


async def test_live_ingest_and_retrieval_with_real_embeddings(
    session: AsyncSession,
) -> None:
    settings = _live_settings()
    embedder = get_embedder(settings)
    assert isinstance(embedder, LMStudioEmbedder)
    games = parse_pgn_games(SAMPLE_PGN)
    assert len(games) == 3

    ids: list[int] = []
    try:
        ids = await ingest_pgn_text(
            session, SAMPLE_PGN, source=LIVE_SOURCE, embedder=embedder
        )
        await session.commit()
        assert len(ids) == 3

        rows = (
            await session.scalars(
                select(AnnotatedGame).where(AnnotatedGame.id.in_(ids))
            )
        ).all()
        assert len(rows) == 3
        assert all(row.source == LIVE_SOURCE for row in rows)
        for row in rows:
            assert row.embedding is not None
            assert len(row.embedding) == EXPECTED_DIM
            assert _norm(row.embedding) == pytest.approx(1.0, abs=1e-6)

        texts_a = await find_similar_positions(
            _final_fen(games[0]), top_k=2, session=session, embedder=embedder
        )
        texts_b = await find_similar_positions(
            _final_fen(games[1]), top_k=2, session=session, embedder=embedder
        )

        assert texts_a and "Итальянская партия" in texts_a[0], texts_a
        assert texts_b and "Ферзевый гамбит" in texts_b[0], texts_b
        print(
            f"\nlive e2e ids={ids} retrieval_a={texts_a[0]!r} "
            f"retrieval_b={texts_b[0]!r}"
        )
    finally:
        if ids:
            await session.execute(
                AnnotatedGame.__table__.delete().where(AnnotatedGame.id.in_(ids))
            )
            await session.commit()
