"""Интеграционные тесты `PATCH /v1/commentaries/{id}` (T-021b).

Проверяем полный сценарий: комментарий создан → отредактирован через API →
`human_edited` выставлен, а в `edits` лежит пара «оригинал → правка».
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Commentary, Edit, Game, Level
from app.main import create_app

SHORT_PGN = "1. d4 Nf6 2. c4 e6 1/2-1/2"
ORIGINAL_TEXT = "Белые разыграли ферзевый гамбит."
NEW_TEXT = "Белые разыграли ферзевый гамбит и получили перевес в центре."


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


async def _seed_commentary() -> tuple[int, int]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(pgn=SHORT_PGN)
            session.add(game)
            await session.flush()
            commentary = Commentary(
                game_id=game.id,
                level=Level.club,
                text=ORIGINAL_TEXT,
                model="test-model",
                tokens_in=10,
                tokens_out=20,
                validated=True,
            )
            session.add(commentary)
            await session.flush()
            await session.commit()
            return game.id, commentary.id
    finally:
        await engine.dispose()


async def _read_state(
    commentary_id: int,
) -> tuple[Commentary | None, list[tuple[str, str, int | None]]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            row = await session.get(Commentary, commentary_id)
            edits = (
                await session.scalars(
                    select(Edit)
                    .where(Edit.commentary_id == commentary_id)
                    .order_by(Edit.id)
                )
            ).all()
            return row, [
                (edit.original_text, edit.edited_text, edit.editor_id)
                for edit in edits
            ]
    finally:
        await engine.dispose()


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(Game.__table__.delete().where(Game.id == game_id))
    finally:
        await engine.dispose()


def test_patch_updates_text_sets_human_edited_and_writes_edit() -> None:
    game_id, commentary_id = asyncio.run(_seed_commentary())
    try:
        with _client() as client:
            response = client.patch(
                f"/v1/commentaries/{commentary_id}", json={"text": NEW_TEXT}
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["commentary_id"] == commentary_id
        assert body["game_id"] == game_id
        assert body["text"] == NEW_TEXT
        assert body["human_edited"] is True
        assert body["validated"] is True
        assert body["edit_id"] > 0

        row, edits = asyncio.run(_read_state(commentary_id))
        assert row is not None
        assert row.text == NEW_TEXT
        assert row.human_edited is True
        assert len(edits) == 1
        assert edits[0] == (ORIGINAL_TEXT, NEW_TEXT, None)
    finally:
        asyncio.run(_delete_game(game_id))


def test_second_patch_appends_another_edit() -> None:
    game_id, commentary_id = asyncio.run(_seed_commentary())
    try:
        with _client() as client:
            first = client.patch(
                f"/v1/commentaries/{commentary_id}", json={"text": NEW_TEXT}
            )
            second = client.patch(
                f"/v1/commentaries/{commentary_id}",
                json={"text": "Третий вариант текста."},
            )

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["edit_id"] != second.json()["edit_id"]

        row, edits = asyncio.run(_read_state(commentary_id))
        assert row is not None
        assert [original for original, _, _ in edits] == [
            ORIGINAL_TEXT,
            NEW_TEXT,
        ]
        assert edits[-1][1] == "Третий вариант текста."
    finally:
        asyncio.run(_delete_game(game_id))


def test_patch_unknown_commentary_returns_404() -> None:
    with _client() as client:
        response = client.patch(
            "/v1/commentaries/999999999", json={"text": NEW_TEXT}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_patch_rejects_blank_text() -> None:
    with _client() as client:
        response = client.patch(
            "/v1/commentaries/1", json={"text": "   \n\t  "}
        )

    assert response.status_code == 422
