"""Интеграционные тесты `POST /v1/analyze` (T-009).

Проверяется то, что нельзя проверить юнитами:

* партия из `tests/fixtures/example.pgn` (53 полухода) на уровне «клуб»
  разбирается **≤ 30 с** (SLA SPEC §9, KI-014);
* в `games` появляется запись, в `moves` — 53 строки с оценками,
  классификацией и глубиной движка;
* битый PGN → HTTP 400 (а не 500), неизвестный уровень → 422.

Тесты требуют PostgreSQL и Stockfish; при их отсутствии — `skipped`
(в CI оба есть).
"""

from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Game, Move
from app.main import create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")

#: SLA из SPEC §9: партия из 40 ходов ≤ 30 с (у нас 53 полухода).
SLA_LIMIT_MS = 30_000
CLASSIFICATIONS = {"best", "brilliant", "good", "inaccuracy", "mistake", "blunder"}


def _engine_available() -> bool:
    settings = get_settings()
    candidate = Path(settings.stockfish_path)
    return candidate.is_file() or shutil.which(settings.stockfish_path) is not None


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
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
    pytest.mark.skipif(not _engine_available(), reason="Stockfish не найден (KI-006)"),
]


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    # Высокий лимит: тесты делают несколько запросов подряд.
    settings = get_settings().model_copy(update={"rate_limit_requests": 1000})
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


async def _read_game(game_id: int) -> tuple[Game | None, list[Move]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = await session.get(Game, game_id)
            moves = (
                await session.scalars(
                    select(Move).where(Move.game_id == game_id).order_by(Move.ply)
                )
            ).all()
    finally:
        await engine.dispose()
    return game, list(moves)


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(delete(Game).where(Game.id == game_id))
    finally:
        await engine.dispose()


def test_analyze_example_pgn_meets_sla_and_persists(client: TestClient) -> None:
    started = time.perf_counter()
    response = client.post(
        "/v1/analyze", json={"pgn": EXAMPLE_PGN, "level": "club"}
    )
    wall_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "completed"
    assert body["analysis_id"].isdigit()
    assert body["moves_analyzed"] == 53
    assert body["meta"] == {
        "white": "Gukesh, D.",
        "black": "Keymer, Vincent",
        "eco": "E21",
    }
    assert set(body["summary"]) == {"blunders", "mistakes", "inaccuracies"}
    assert body["time_taken_ms"] <= SLA_LIMIT_MS
    assert wall_ms <= SLA_LIMIT_MS, f"SLA нарушен: {wall_ms:.0f} мс"

    game_id = int(body["analysis_id"])
    try:
        game, moves = asyncio.run(_read_game(game_id))

        assert game is not None
        assert game.eco_code == "E21"
        assert game.phase_breakdown == {
            "opening_end_ply": 30,
            "endgame_start_ply": None,
        }
        assert game.pgn.strip() == EXAMPLE_PGN.strip()

        assert len(moves) == 53
        assert [move.ply for move in moves] == list(range(1, 54))
        for move in moves:
            assert move.classification in CLASSIFICATIONS
            assert move.eval_cp is not None or move.eval_mate is not None
            assert move.best_move_san
            assert move.delta_cp is not None and move.delta_cp >= 0
            assert move.engine_depth is not None and move.engine_depth >= 1
    finally:
        asyncio.run(_delete_game(game_id))


def test_analyze_rejects_garbage_pgn(client: TestClient) -> None:
    response = client.post(
        "/v1/analyze", json={"pgn": "это не PGN, а просто текст", "level": "club"}
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_pgn"


def test_analyze_rejects_empty_pgn(client: TestClient) -> None:
    response = client.post("/v1/analyze", json={"pgn": "", "level": "club"})

    assert response.status_code == 422


def test_analyze_rejects_unknown_level(client: TestClient) -> None:
    response = client.post(
        "/v1/analyze", json={"pgn": EXAMPLE_PGN, "level": "master"}
    )

    assert response.status_code == 422
