"""Интеграционные тесты `GET /v1/commentaries/{id}/export/pdf` (T-107).

Проверяем:

* 200 + `application/pdf` + `Content-Disposition: attachment`; файл
  начинается с `%PDF-`, pypdf извлекает шапку, ходы и комментарий;
* 401 без токена; 403/404 по изоляции данных (чужая партия → 404);
* администратор может выгрузить чужой комментарий;
* битый legacy-PGN не ломает экспорт (шапка с прочерками, ходы из БД).

Требуется PostgreSQL; без него модуль скипается. Если нет TTF-шрифта,
тесты, проверяющие содержимое PDF, скипаются (см. `PDF_FONT_PATH`).
"""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.db.models import (
    AdminRole,
    Commentary,
    Game,
    Level,
    Move,
    User,
)
from app.errors import PdfFontUnavailableError
from app.export.pdf import find_pdf_font
from app.ingestion import parse_pgn
from app.main import create_app

PGN = (
    "[Event \"Клубный турнир\"]\n"
    "[White \"Иван Петров\"]\n"
    "[Black \"Пётр Сидоров\"]\n"
    "[Date \"2026.10.29\"]\n"
    "[Result \"1/2-1/2\"]\n\n"
    "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 1/2-1/2"
)
COMMENTARY = (
    "Первый абзац комментария с кириллицей.\n\n"
    "Второй абзац: после 1. e4 e5 позиция симметрична."
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


def _font_available() -> bool:
    try:
        find_pdf_font(get_settings())
    except PdfFontUnavailableError:
        return False
    return True


pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
    pytest.mark.skipif(
        not _font_available(), reason="нет TTF-шрифта для PDF"
    ),
]


def _db_url() -> str:
    return get_settings().database_url


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _insert_user(
    *, email: str, role: AdminRole, level: Level = Level.club
) -> User:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = User(
                email=email,
                role=role,
                level=level,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


async def _seed_game(
    *, owner_id: int | None, commentary_text: str, pgn: str = PGN
) -> dict[str, int]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(user_id=owner_id, pgn=pgn)
            session.add(game)
            await session.flush()
            try:
                parsed = parse_pgn(pgn)
            except Exception:
                parsed = None
            if parsed is not None:
                for move in parsed.moves:
                    session.add(
                        Move(
                            game_id=game.id,
                            ply=move.ply,
                            san=move.san,
                            fen=move.fen,
                            eval_cp=0,
                            best_move_san=move.san,
                            delta_cp=0,
                            classification="good",
                            engine_depth=10,
                        )
                    )
            commentary = Commentary(
                game_id=game.id,
                user_id=owner_id,
                level=Level.club,
                text=commentary_text,
                model="test-model",
                validated=True,
                human_edited=False,
                created_at=datetime.now(UTC),
            )
            session.add(commentary)
            await session.commit()
            return {
                "game_id": game.id,
                "commentary_id": commentary.id,
            }
    finally:
        await engine.dispose()


async def _delete_games(game_ids: list[int]) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            for game_id in game_ids:
                await connection.execute(
                    Game.__table__.delete().where(Game.id == game_id)
                )
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
            "engine_cache_enabled": False,
            # Для проверки 401/404 нужна настоящая JWT-аутентификация.
            "test_auth_bypass": False,
        }
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings()), follow_redirects=False) as c:
        yield c


@pytest.fixture()
def people(client: TestClient) -> Iterator[dict[str, User]]:
    suffix = uuid4().hex
    users = {
        "owner": asyncio.run(
            _insert_user(
                email=f"pdf-owner-{suffix}@example.com",
                role=AdminRole.user,
            )
        ),
        "other": asyncio.run(
            _insert_user(
                email=f"pdf-other-{suffix}@example.com",
                role=AdminRole.user,
            )
        ),
        "admin": asyncio.run(
            _insert_user(
                email=f"pdf-admin-{suffix}@example.com",
                role=AdminRole.admin,
            )
        ),
    }
    try:
        yield users
    finally:
        asyncio.run(_delete_users([user.email for user in users.values()]))


def _bearer(user: User, settings: Settings | None = None) -> dict[str, str]:
    token = create_access_token(user, settings or _settings())
    return {"Authorization": f"Bearer {token}"}


def _pdf_text(response: Any) -> str:
    reader = PdfReader(io.BytesIO(response.content))
    return "".join(page.extract_text() or "" for page in reader.pages)


def test_export_pdf_returns_valid_pdf(
    client: TestClient, people: dict[str, User]
) -> None:
    seeded = asyncio.run(
        _seed_game(owner_id=people["owner"].id, commentary_text=COMMENTARY)
    )
    try:
        response = client.get(
            f"/v1/commentaries/{seeded['commentary_id']}/export/pdf",
            headers=_bearer(people["owner"]),
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith(
            "application/pdf"
        )
        disposition = response.headers["content-disposition"]
        assert "attachment" in disposition
        assert f"commentary-{seeded['commentary_id']}.pdf" in disposition
        assert response.content.startswith(b"%PDF-")
        assert response.content.rstrip().endswith(b"%%EOF")

        text = _pdf_text(response)
        for expected in (
            "Иван Петров",
            "Пётр Сидоров",
            "Клубный турнир",
            "1. e4 e5 2. Nf3 Nc6",
            "Первый абзац комментария",
            "Второй абзац",
        ):
            assert expected in text, expected
    finally:
        asyncio.run(_delete_games([seeded["game_id"]]))


def test_export_pdf_requires_auth(
    client: TestClient, people: dict[str, User]
) -> None:
    seeded = asyncio.run(
        _seed_game(owner_id=people["owner"].id, commentary_text=COMMENTARY)
    )
    try:
        response = client.get(
            f"/v1/commentaries/{seeded['commentary_id']}/export/pdf"
        )
        assert response.status_code == 401
        assert response.json()["code"] in {"unauthorized", "auth_required"}
    finally:
        asyncio.run(_delete_games([seeded["game_id"]]))


def test_export_pdf_hides_foreign_commentary(
    client: TestClient, people: dict[str, User]
) -> None:
    seeded = asyncio.run(
        _seed_game(owner_id=people["owner"].id, commentary_text=COMMENTARY)
    )
    try:
        response = client.get(
            f"/v1/commentaries/{seeded['commentary_id']}/export/pdf",
            headers=_bearer(people["other"]),
        )
        assert response.status_code == 404
    finally:
        asyncio.run(_delete_games([seeded["game_id"]]))


def test_export_pdf_missing_commentary(
    client: TestClient, people: dict[str, User]
) -> None:
    response = client.get(
        "/v1/commentaries/999999999/export/pdf",
        headers=_bearer(people["owner"]),
    )
    assert response.status_code == 404


def test_admin_can_export_foreign_commentary(
    client: TestClient, people: dict[str, User]
) -> None:
    seeded = asyncio.run(
        _seed_game(owner_id=people["owner"].id, commentary_text=COMMENTARY)
    )
    try:
        response = client.get(
            f"/v1/commentaries/{seeded['commentary_id']}/export/pdf",
            headers=_bearer(people["admin"]),
        )
        assert response.status_code == 200, response.text
        assert response.content.startswith(b"%PDF-")
    finally:
        asyncio.run(_delete_games([seeded["game_id"]]))


def test_invalid_pgn_still_exports_pdf(
    client: TestClient, people: dict[str, User]
) -> None:
    """Legacy-PGN не парсится — шапка с прочерками, ходы берутся из БД."""
    bad_pgn = "not a pgn at all"
    seeded = asyncio.run(
        _seed_game(
            owner_id=people["owner"].id,
            commentary_text=COMMENTARY,
            pgn=bad_pgn,
        )
    )
    try:
        response = client.get(
            f"/v1/commentaries/{seeded['commentary_id']}/export/pdf",
            headers=_bearer(people["owner"]),
        )
        assert response.status_code == 200, response.text
        text = _pdf_text(response)
        assert "Первый абзац комментария" in text
        assert "—" in text
    finally:
        asyncio.run(_delete_games([seeded["game_id"]]))
