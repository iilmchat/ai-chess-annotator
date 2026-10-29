"""Интеграционные тесты `GET /v1/games/{id}` (T-031)."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

import chess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Commentary, Game, Level, Move
from app.ingestion import parse_pgn
from app.main import create_app

SHORT_PGN = (
    '[Event "Saved"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")

#: Партия для «legacy»-строк: ECO есть в заголовке PGN (C50).
LEGACY_PGN = """[Event "Legacy restore"]
[Site "?"]
[Date "2024.01.01"]
[Round "1"]
[White "Legacy White"]
[Black "Legacy Black"]
[Result "*"]
[ECO "C50"]

1. e4 e5 2. Nf3 Nc6 1/2-1/2
"""

#: FEN-старт: чёрный король на e8, белая пешка на e2.
FEN_PGN = """[Event "FEN start"]
[Site "?"]
[Date "2024.01.02"]
[Round "1"]
[White "Fen White"]
[Black "Fen Black"]
[SetUp "1"]
[FEN "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"]

1. e4 Kd7 1/2-1/2
"""
FEN_START = "4k3/8/8/8/8/8/4P3/4K3 w - - 0 1"

#: Явная метка времени: ответ должен вернуть ровно её, без `now()`.
COMMENTARY_CREATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)



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


def _engine_available() -> bool:
    settings = get_settings()
    candidate = Path(settings.stockfish_path)
    return candidate.is_file() or shutil.which(settings.stockfish_path) is not None



def _client() -> TestClient:
    settings = get_settings().model_copy(
        update={"rate_limit_requests": 1000, "llm_warmup_enabled": False}
    )
    return TestClient(create_app(settings))


def test_get_game_returns_saved_analysis_and_commentaries() -> None:
    with _client() as client:
        analyze = client.post(
            "/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"}
        )
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            asyncio.run(_seed_commentary(analysis_id))
            response = client.get(f"/v1/games/{analysis_id}")

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["analysis_id"] == str(analysis_id)
            assert body["status"] == "completed"
            assert body["analysis_level"] == "club"
            assert body["moves_analyzed"] == 4
            assert body["initial_fen"].startswith("rnbqkbnr")
            assert body["moves"][0]["san"] == "e4"
            assert "4P3" in body["moves"][0]["fen"]
            assert body["time_taken_ms"] >= 0
            assert len(body["commentaries"]) == 1
            item = body["commentaries"][0]
            assert item["level"] == "club"
            assert item["text"] == "Сохранённый комментарий."
            assert item["human_edited"] is False
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_list_games_contains_saved_game() -> None:
    with _client() as client:
        analyze = client.post(
            "/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"}
        )
        assert analyze.status_code == 200, analyze.text
        analysis_id = int(analyze.json()["analysis_id"])
        try:
            response = client.get("/v1/games", params={"limit": 50})

            assert response.status_code == 200, response.text
            item = next(
                row for row in response.json() if row["id"] == analysis_id
            )
            assert item["white"] == "White"
            assert item["black"] == "Black"
            assert item["event"] == "Saved"
            assert item["level"] == "club"
            assert item["created_at"]
        finally:
            asyncio.run(_delete_game(analysis_id))


def test_get_unknown_game_returns_404() -> None:
    with _client() as client:
        response = client.get("/v1/games/999999999")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def _seed_commentary(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            session.add(
                Commentary(
                    game_id=game_id,
                    level=Level.club,
                    text="Сохранённый комментарий.",
                    model="test-model",
                    tokens_in=10,
                    tokens_out=5,
                    validated=True,
                )
            )
            await session.commit()
    finally:
        await engine.dispose()


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(Game.__table__.delete().where(Game.id == game_id))
    finally:
        await engine.dispose()


# --- parity с POST /v1/analyze -------------------------------------------


@pytest.mark.skipif(not _engine_available(), reason="Stockfish не найден (KI-006)")
def test_get_game_moves_match_analyze_deeply() -> None:
    """GET повторяет ответ analyze целиком (глубокое сравнение `moves`)."""
    with _client() as client:
        analyze = client.post(
            "/v1/analyze", json={"pgn": EXAMPLE_PGN, "level": "club"}
        )
        assert analyze.status_code == 200, analyze.text
        analyzed = analyze.json()
        game_id = int(analyzed["analysis_id"])
        try:
            response = client.get(f"/v1/games/{game_id}")
            assert response.status_code == 200, response.text
            restored = response.json()

            # Контракт = ответ analyze + комментарии/created_at: ни лишних,
            # ни пропущенных полей.
            assert set(restored) == set(analyzed) | {"commentaries", "created_at"}
            for field in (
                "analysis_id",
                "status",
                "meta",
                "initial_fen",
                "analysis_level",
                "moves_analyzed",
                "time_taken_ms",
                "summary",
            ):
                assert restored[field] == analyzed[field], f"поле {field}"

            # Полное глубокое сравнение всего списка ходов: ply/san/uci/fen/
            # оценки/best_move_san/delta_cp/classification/motifs/
            # recommended_move.
            assert restored["moves"] == analyzed["moves"]
            assert len(restored["moves"]) == analyzed["moves_analyzed"] == 53
            assert restored["commentaries"] == []
        finally:
            asyncio.run(_delete_game(game_id))


# --- комментарии ----------------------------------------------------------


def test_get_game_returns_commentaries_in_creation_order() -> None:
    game_id = asyncio.run(_seed_legacy_game())
    try:
        commentary_ids = asyncio.run(_seed_commentaries(game_id))

        with _client() as client:
            response = client.get(f"/v1/games/{game_id}")

        assert response.status_code == 200, response.text
        body = response.json()
        items = body["commentaries"]

        # Порядок — по возрастанию `commentaries.id`.
        assert [item["id"] for item in items] == commentary_ids

        first, second = items
        assert first["id"] == commentary_ids[0]
        assert first["level"] == "club"
        assert first["text"] == "Первый комментарий: план на игру."
        assert first["model"] == "qwen3-4b-2507"
        assert first["tokens_in"] == 120
        assert first["tokens_out"] == 40
        assert first["validated"] is True
        assert first["human_edited"] is False
        assert _parse_dt(first["created_at"]) == COMMENTARY_CREATED_AT

        assert second["id"] == commentary_ids[1]
        assert second["level"] == "gm"
        assert second["text"] == "Второй комментарий: глубокий разбор."
        assert second["model"] == "deepseek-chat"
        # NULL-токены отдаются как 0 (контракт `ge=0`).
        assert second["tokens_in"] == 0
        assert second["tokens_out"] == 0
        assert second["validated"] is False
        assert second["human_edited"] is False
        assert _parse_dt(second["created_at"]) == COMMENTARY_CREATED_AT

        assert body["analysis_level"] == "club"
        assert body["meta"] == {
            "white": "Legacy White",
            "black": "Legacy Black",
            "eco": "C50",
        }
    finally:
        asyncio.run(_delete_game(game_id))


# --- FEN-старт ------------------------------------------------------------


def test_get_game_reconstructs_uci_from_fen_start() -> None:
    """UCI считается от `initial_fen`, а не от стандартной расстановки."""
    game_id = asyncio.run(_seed_fen_game())
    try:
        with _client() as client:
            response = client.get(f"/v1/games/{game_id}")

        assert response.status_code == 200, response.text
        body = response.json()

        assert body["initial_fen"] == FEN_START
        assert body["analysis_level"] == "club"
        assert body["time_taken_ms"] == 1234
        assert body["moves_analyzed"] == 2
        assert body["meta"] == {
            "white": "Fen White",
            "black": "Fen Black",
            "eco": "B00",
        }
        # `Kd7` (e8d7) нелегален из стандартной расстановки: успешное
        # восстановление доказывает, что реплей начат с FEN партии.
        assert [(move["ply"], move["san"], move["uci"]) for move in body["moves"]] == [
            (1, "e4", "e2e4"),
            (2, "Kd7", "e8d7"),
        ]
        assert [move["fen"] for move in body["moves"]] == _expected_fen_sequence()
        assert all(move["recommended_move"] is None for move in body["moves"])
        assert body["commentaries"] == []
    finally:
        asyncio.run(_delete_game(game_id))


# --- 404 ------------------------------------------------------------------


def test_get_unknown_game_returns_exact_not_found_body() -> None:
    missing_id = 999_999_999
    with _client() as client:
        response = client.get(f"/v1/games/{missing_id}")

    assert response.status_code == 404
    assert response.json() == {
        "detail": f"Партия {missing_id} не найдена",
        "code": "not_found",
    }


def test_get_game_without_analysis_returns_404() -> None:
    game_id = asyncio.run(_seed_game_without_moves())
    try:
        with _client() as client:
            response = client.get(f"/v1/games/{game_id}")

        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert "нет анализа" in body["detail"]
    finally:
        asyncio.run(_delete_game(game_id))


# --- подготовка тестовых строк -------------------------------------------


def _parse_dt(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _expected_fen_sequence() -> list[str]:
    board = chess.Board(FEN_START)
    fens: list[str] = []
    for san in ("e4", "Kd7"):
        board.push_san(san)
        fens.append(board.fen())
    return fens


async def _seed_legacy_game() -> int:
    """Создать партию с сохранённым уровнем, 2 ходами и ECO из заголовка."""
    parsed = parse_pgn(LEGACY_PGN)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(
                pgn=LEGACY_PGN,
                eco_code=None,
                phase_breakdown=parsed.phase_breakdown.model_dump(),
                analysis_level=Level.club,
                analysis_time_ms=None,
            )
            session.add(game)
            await session.flush()
            session.add_all(
                [
                    Move(
                        game_id=game.id,
                        ply=move.ply,
                        san=move.san,
                        fen=move.fen,
                        eval_cp=30 + move.ply,
                        eval_mate=None,
                        best_move_san=move.san,
                        delta_cp=0,
                        classification="good",
                        engine_depth=16,
                        multipv=None,
                    )
                    for move in parsed.moves
                ]
            )
            await session.commit()
            game_id = game.id
    finally:
        await engine.dispose()
    return game_id


async def _seed_fen_game() -> int:
    """Создать партию с FEN-стартом и сохранёнными ходами/уровнем."""
    parsed = parse_pgn(FEN_PGN)
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(
                pgn=FEN_PGN,
                eco_code=None,
                phase_breakdown=parsed.phase_breakdown.model_dump(),
                analysis_level=Level.club,
                analysis_time_ms=1234,
            )
            session.add(game)
            await session.flush()
            session.add_all(
                [
                    Move(
                        game_id=game.id,
                        ply=move.ply,
                        san=move.san,
                        fen=move.fen,
                        eval_cp=0,
                        eval_mate=None,
                        best_move_san=move.san,
                        delta_cp=0,
                        classification="good",
                        engine_depth=10,
                        multipv=None,
                    )
                    for move in parsed.moves
                ]
            )
            await session.commit()
            game_id = game.id
    finally:
        await engine.dispose()
    return game_id


async def _seed_game_without_moves() -> int:
    """Партия есть в `games`, но анализа ходов нет — GET обязан дать 404."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(
                pgn=SHORT_PGN,
                analysis_level=Level.club,
                analysis_time_ms=None,
            )
            session.add(game)
            await session.commit()
            game_id = game.id
    finally:
        await engine.dispose()
    return game_id


async def _seed_commentaries(game_id: int) -> list[int]:
    """Добавить два комментария и вернуть их id в порядке создания."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            first = Commentary(
                game_id=game_id,
                level=Level.club,
                text="Первый комментарий: план на игру.",
                model="qwen3-4b-2507",
                tokens_in=120,
                tokens_out=40,
                validated=True,
                created_at=COMMENTARY_CREATED_AT,
            )
            second = Commentary(
                game_id=game_id,
                level=Level.gm,
                text="Второй комментарий: глубокий разбор.",
                model="deepseek-chat",
                tokens_in=None,
                tokens_out=None,
                validated=False,
                created_at=COMMENTARY_CREATED_AT,
            )
            session.add_all([first, second])
            await session.commit()
            ids = [first.id, second.id]
    finally:
        await engine.dispose()
    return ids
