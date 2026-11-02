"""Интеграционные тесты RAG: ingest, retrieval, контекст для комментария (T-017)."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncIterator

import chess
import chess.pgn
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import AnnotatedGame, Game, Move
from app.engine.models import MoveAnalysis
from app.errors import InvalidFenError
from app.ingestion import parse_pgn
from app.llm.base import BaseLLMClient, LLMResult, LLMUsage
from app.main import create_app
from app.rag import find_similar_positions, ingest_pgn_text, parse_pgn_games
from app.rag.context import make_rag_context_builder

TWO_GAMES_PGN = """[Event "Test A"]
[Site "?"]
[Date "2024.01.15"]
[White "White A"]
[Black "Black A"]
[Result "1-0"]

1. e4 e5 2. Nf3 {Хороший развивающий ход} Nc6 3. Bb5 {Давление на c6} 1-0

[Event "Test B"]
[Site "?"]
[Date "2023.05.02"]
[White "White B"]
[Black "Black B"]
[Result "0-1"]

1. d4 d5 2. c4 {Гамбит} e6 3. Nc3 0-1
"""


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


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


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


def _final_fen_of_first_game() -> str:
    game = chess.pgn.read_game(io.StringIO(TWO_GAMES_PGN))
    assert game is not None
    board = game.board()
    for move in game.mainline_moves():
        board.push(move)
    return board.fen()


def _client() -> TestClient:
    settings = get_settings().model_copy(
        update={"rate_limit_requests": 1000, "llm_warmup_enabled": False}
    )
    return TestClient(create_app(settings))


# --- разбор PGN -----------------------------------------------------------


def test_parse_pgn_games_reads_all_games() -> None:
    games = parse_pgn_games(TWO_GAMES_PGN)

    assert len(games) == 2
    assert games[0].headers["Event"] == "Test A"
    assert games[1].headers["White"] == "White B"


# --- ingest + retrieval на уровне сервиса --------------------------------


async def test_ingest_saves_annotations_and_embeddings(
    session: AsyncSession,
) -> None:
    ids = await ingest_pgn_text(session, TWO_GAMES_PGN, source="unit-test")

    assert len(ids) == 2
    rows = (
        await session.scalars(
            select(AnnotatedGame).where(AnnotatedGame.id.in_(ids))
        )
    ).all()
    assert len(rows) == 2
    assert all(row.embedding is not None for row in rows)
    annotations = " ".join(row.annotation_text or "" for row in rows)
    assert "Давление на c6" in annotations
    assert "Гамбит" in annotations
    assert {row.year for row in rows} == {2024, 2023}
    assert rows[0].source == "unit-test"


async def test_find_similar_positions_returns_matching_game(
    session: AsyncSession,
) -> None:
    await ingest_pgn_text(session, TWO_GAMES_PGN, source="unit-test")

    texts = await find_similar_positions(
        _final_fen_of_first_game(), top_k=2, session=session
    )

    assert texts, "retrieval ничего не нашёл"
    assert "Давление на c6" in texts[0], texts


async def test_find_similar_positions_validates_fen(session: AsyncSession) -> None:
    with pytest.raises(InvalidFenError):
        await find_similar_positions("not-a-fen", session=session)


# --- HTTP /v1/ingest ------------------------------------------------------


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": get_settings().admin_api_key or ""}


def test_ingest_endpoint_loads_games_and_retrieval_finds_them() -> None:
    with _client() as client:
        response = client.post(
            "/v1/ingest",
            files={"file": ("games.pgn", TWO_GAMES_PGN.encode("utf-8"), "text/plain")},
            data={"source": "api-test"},
            headers=_admin_headers(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ingested"] == 2
        ids = body["ids"]
        assert len(ids) == 2

    # Вне TestClient: у приложения свой event loop, глобальный async-engine
    # нельзя переиспользовать из другого цикла (после выхода пул закрыт).
    try:
        texts = asyncio.run(
            find_similar_positions(_final_fen_of_first_game(), top_k=2)
        )
        assert texts
        assert "Давление на c6" in texts[0]
    finally:
        asyncio.run(_delete_annotated(ids))


def test_ingest_rejects_empty_file() -> None:
    with _client() as client:
        response = client.post(
            "/v1/ingest",
            files={"file": ("empty.pgn", b"", "text/plain")},
            headers=_admin_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_pgn"


def test_ingest_requires_admin_token() -> None:
    with _client() as client:
        response = client.post(
            "/v1/ingest",
            files={"file": ("games.pgn", TWO_GAMES_PGN.encode("utf-8"), "text/plain")},
        )
        bad = client.post(
            "/v1/ingest",
            files={"file": ("games.pgn", TWO_GAMES_PGN.encode("utf-8"), "text/plain")},
            headers={"X-Admin-Token": "wrong"},
        )

    assert response.status_code == 401
    assert bad.status_code == 401


# --- RAG-контекст для комментария ----------------------------------------


async def test_rag_context_builder_returns_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_find(
        fen: str, top_k: int = 3, **_: object
    ) -> list[str]:
        return ["Похожая партия: давление на c6"]

    monkeypatch.setattr("app.rag.context.find_similar_positions", fake_find)

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> bool:
            return False

    builder = make_rag_context_builder(session_factory=lambda: _Session())  # type: ignore[arg-type,return-value]
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

    context = await builder(parsed, [analysis])

    assert context is not None
    assert "RAG" in context
    assert "Похожая партия" in context
    assert "## Ход 1 белых (d4)" in context
    assert "полуход" not in context.lower()


async def test_rag_context_builder_returns_none_without_mistakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = make_rag_context_builder()
    parsed = parse_pgn("1. d4 Nf6 2. c4 e6 1/2-1/2")
    analysis = MoveAnalysis(
        ply=1,
        played_move_san="d4",
        played_move_uci="d2d4",
        eval_before=0,
        eval_after=0,
        best_move_uci="d2d4",
        best_move_san="d4",
        delta_cp=0,
        classification="best",
        lines=[],
    )

    assert await builder(parsed, [analysis]) is None


async def _delete_annotated(ids: list[int]) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                AnnotatedGame.__table__.delete().where(AnnotatedGame.id.in_(ids))
            )
    finally:
        await engine.dispose()


# --- RAG в /v1/commentary для gm -----------------------------------------


class _LegalFakeLLM(BaseLLMClient):
    def __init__(self) -> None:
        super().__init__(model="fake-model")
        self.prompts: list[str] = []

    @property
    def provider(self) -> str:
        return "fake"

    async def complete(
        self, prompt: str, system_prompt: str | None = None
    ) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text="Партия началась с d4 Nf6.",
            usage=LLMUsage(
                provider="fake", model="fake-model", tokens_in=10, tokens_out=5
            ),
        )


async def _seed_game(commit: bool = True) -> int:
    parsed = parse_pgn("1. d4 Nf6 2. c4 e6 1/2-1/2")
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(
                pgn="1. d4 Nf6 2. c4 e6 1/2-1/2",
                eco_code=parsed.eco_code,
                phase_breakdown=parsed.phase_breakdown.model_dump(),
            )
            session.add(game)
            await session.flush()
            for index, move in enumerate(parsed.moves):
                session.add(
                    Move(
                        game_id=game.id,
                        ply=move.ply,
                        san=move.san,
                        fen=move.fen,
                        eval_cp=20,
                        best_move_san=move.san,
                        delta_cp=150 if index == 0 else 0,
                        classification="mistake" if index == 0 else "good",
                        engine_depth=12,
                    )
                )
            if commit:
                await session.commit()
            else:
                await session.flush()
            game_id = game.id
    finally:
        await engine.dispose()
    return game_id


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(Game.__table__.delete().where(Game.id == game_id))
    finally:
        await engine.dispose()


def test_gm_commentary_includes_rag_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    game_id = asyncio.run(_seed_game())
    fake_llm = _LegalFakeLLM()

    async def fake_rag(parsed: object, analyses: object) -> str:
        return "# Похожие позиции из базы аннотаций (RAG)\n\n- Тестовая аннотация про d4"

    monkeypatch.setattr(
        "app.commentary.service.get_llm_client", lambda level, cfg: fake_llm
    )
    monkeypatch.setattr(
        "app.main.make_rag_context_builder", lambda cfg: fake_rag
    )

    settings = get_settings().model_copy(
        update={"rate_limit_requests": 1000, "llm_warmup_enabled": False}
    )
    try:
        with TestClient(create_app(settings)) as client:
            gm = client.post(
                "/v1/commentary", json={"analysis_id": game_id, "level": "gm"}
            )
            assert gm.status_code == 200, gm.text

            club = client.post(
                "/v1/commentary", json={"analysis_id": game_id, "level": "club"}
            )
            assert club.status_code == 200, club.text
    finally:
        asyncio.run(_delete_game(game_id))

    assert len(fake_llm.prompts) == 2
    assert "Тестовая аннотация про d4" in fake_llm.prompts[0], "gm должен видеть RAG"
    assert "Тестовая аннотация про d4" not in fake_llm.prompts[1], "club — без RAG"
