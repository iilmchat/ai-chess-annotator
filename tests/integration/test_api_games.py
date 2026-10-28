"""Интеграционные тесты `GET /v1/games/{id}` (T-031)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Commentary, Game, Level
from app.main import create_app

SHORT_PGN = (
    '[Event "Saved"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)


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
