"""Изоляция данных между пользователями (KI-035, T-028).

Тесты используют **реальную** аутентификацию: приложение собирается с
``test_auth_bypass=False``, пользователи A/B и администратор проходят через
``/auth/register`` + ``/auth/login``. Проверяется матрица доступа:

* партии и комментарии принадлежат владельцу (`games.user_id`,
  `commentaries.user_id`, `edits.user_id`);
* чужие записи неотличимы от несуществующих (HTTP 404, а не 403);
* legacy-записи с ``user_id IS NULL`` видит только администратор;
* администратор читает и комментирует любую партию.

Тесты требуют PostgreSQL; тест `POST /v1/analyze` дополнительно требует
Stockfish (иначе скипается).
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import AdminRole, Commentary, Edit, Game, Level, Move, User
from app.ingestion import parse_pgn
from app.main import create_app

#: Короткая легальная партия: 4 полухода — минимум работы Stockfish.
SHORT_PGN = (
    '[Event "Isolation"]\n[White "White"]\n[Black "Black"]\n'
    '[Result "1/2-1/2"]\n\n1. e4 e5 2. Nf3 Nc6 1/2-1/2'
)
PASSWORD = "isolation-password-123"
PATCH_TEXT = "Отредактированный комментарий пользователя A."


def _db_available() -> bool:
    """Проверить доступность PostgreSQL (как в остальных интеграционных)."""

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


def _stockfish_available() -> bool:
    """Есть ли бинарник Stockfish по пути из настроек (KI-006)."""
    settings = get_settings()
    candidate = Path(settings.stockfish_path)
    return candidate.is_file() or shutil.which(settings.stockfish_path) is not None


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


def _settings() -> Settings:
    """Приложение с реальной авторизацией и без фоновых подсистем."""
    return Settings(
        _env_file=None,
        app_env="test",
        test_auth_bypass=False,
        rate_limit_enabled=False,
        token_budget_enabled=False,
        llm_warmup_enabled=False,
        prompt_pubsub_enabled=False,
        semantic_cache_enabled=False,
        # `_env_file=None` не читает .env, а skipif выше проверяет путь из
        # get_settings(): без переноса пути приложение искало бы
        # /usr/bin/stockfish на Windows-dev (KI-006).
        stockfish_path=get_settings().stockfish_path,
    )


def _engine() -> Any:
    return create_async_engine(get_settings().database_url, poolclass=NullPool)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client: TestClient, email: str) -> tuple[str, int]:
    response = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["access_token"], int(body["user"]["id"])


def _verify_email(client: TestClient, email: str) -> None:
    """KI-041: пройти по ссылке подтверждения из mock-письма."""
    sender = client.app.state.email_sender
    messages = [
        message
        for message in sender.outbox
        if message.to == email and message.kind == "verify_email"
    ]
    assert messages, f"нет письма подтверждения для {email}"
    response = client.get(
        "/auth/verify-email", params={"token": messages[-1].token}
    )
    assert response.status_code == 200, response.text


@dataclass(frozen=True, slots=True)
class IsolationEnv:
    """Общие для модуля пользователи, токены и seeded-записи."""

    client: TestClient
    user_a: int
    user_b: int
    admin_id: int
    token_a: str
    token_b: str
    token_admin: str
    game_a: int
    game_b: int
    legacy_game: int
    commentary_a: int


@pytest.fixture(scope="module")
def env() -> Iterator[IsolationEnv]:
    """Поднять приложение, зарегистрировать A/B/admin и засеять записи."""
    suffix = uuid.uuid4().hex
    email_a = f"isolation-a-{suffix}@example.com"
    email_b = f"isolation-b-{suffix}@example.com"
    email_admin = f"isolation-admin-{suffix}@example.com"
    emails = [email_a, email_b, email_admin]

    with TestClient(create_app(_settings())) as client:
        for email in emails:
            _register(client, email)
            _verify_email(client, email)
        # Роль нельзя выбрать при регистрации — повышаем напрямую в БД.
        asyncio.run(_promote_admin(email_admin))

        token_a, user_a = _login(client, email_a)
        token_b, user_b = _login(client, email_b)
        token_admin, admin_id = _login(client, email_admin)

        game_a = asyncio.run(_seed_game(user_a))
        game_b = asyncio.run(_seed_game(user_b))
        legacy_game = asyncio.run(_seed_game(None))
        commentary_a = asyncio.run(_seed_commentary(game_a, user_a))

        try:
            yield IsolationEnv(
                client=client,
                user_a=user_a,
                user_b=user_b,
                admin_id=admin_id,
                token_a=token_a,
                token_b=token_b,
                token_admin=token_admin,
                game_a=game_a,
                game_b=game_b,
                legacy_game=legacy_game,
                commentary_a=commentary_a,
            )
        finally:
            asyncio.run(
                _cleanup(
                    user_ids=[user_a, user_b, admin_id],
                    emails=emails,
                    legacy_game=legacy_game,
                )
            )


# ---------------------------------------------------------------------------
# POST /v1/analyze: токен обязателен, владелец сохраняется
# ---------------------------------------------------------------------------


def test_analyze_without_token_returns_401(env: IsolationEnv) -> None:
    response = env.client.post(
        "/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"}
    )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


@pytest.mark.skipif(
    not _stockfish_available(), reason="Stockfish не найден (KI-006)"
)
def test_analyze_with_token_persists_owner(env: IsolationEnv) -> None:
    response = env.client.post(
        "/v1/analyze",
        json={"pgn": SHORT_PGN, "level": "club"},
        headers=_headers(env.token_a),
    )

    assert response.status_code == 200, response.text
    game_id = int(response.json()["analysis_id"])
    owner = asyncio.run(_game_owner(game_id))
    assert owner == env.user_a, "games.user_id должен быть владельцем-вызывающим"


# ---------------------------------------------------------------------------
# GET /v1/games: список ограничен владельцем, admin видит всё
# ---------------------------------------------------------------------------


def test_list_games_is_isolated_per_owner(env: IsolationEnv) -> None:
    list_a = env.client.get(
        "/v1/games", params={"limit": 50}, headers=_headers(env.token_a)
    )
    list_b = env.client.get(
        "/v1/games", params={"limit": 50}, headers=_headers(env.token_b)
    )
    list_admin = env.client.get(
        "/v1/games", params={"limit": 50}, headers=_headers(env.token_admin)
    )

    assert list_a.status_code == 200, list_a.text
    assert list_b.status_code == 200, list_b.text
    assert list_admin.status_code == 200, list_admin.text

    ids_a = {row["id"] for row in list_a.json()}
    ids_b = {row["id"] for row in list_b.json()}
    ids_admin = {row["id"] for row in list_admin.json()}

    # Ровно свои партии: ни чужих, ни legacy-записей без владельца.
    assert ids_a == asyncio.run(_game_ids(env.user_a))
    assert ids_b == asyncio.run(_game_ids(env.user_b))
    assert env.game_a in ids_a and env.game_b not in ids_a
    assert env.game_b in ids_b and env.game_a not in ids_b
    assert env.legacy_game not in ids_a
    assert env.legacy_game not in ids_b

    # Администратор видит все партии, включая legacy.
    assert {env.game_a, env.game_b, env.legacy_game} <= ids_admin


def test_list_games_requires_token(env: IsolationEnv) -> None:
    response = env.client.get("/v1/games")

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# GET /v1/games/{id}: 404 для чужих и legacy, 200 для admin
# ---------------------------------------------------------------------------


def test_get_foreign_game_returns_404_for_user(env: IsolationEnv) -> None:
    response = env.client.get(
        f"/v1/games/{env.game_b}", headers=_headers(env.token_a)
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


def test_admin_can_get_foreign_game(env: IsolationEnv) -> None:
    response = env.client.get(
        f"/v1/games/{env.game_b}", headers=_headers(env.token_admin)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["analysis_id"] == str(env.game_b)
    assert body["moves_analyzed"] == 4


def test_legacy_game_is_admin_only(env: IsolationEnv) -> None:
    denied = env.client.get(
        f"/v1/games/{env.legacy_game}", headers=_headers(env.token_a)
    )
    allowed = env.client.get(
        f"/v1/games/{env.legacy_game}", headers=_headers(env.token_admin)
    )

    assert denied.status_code == 404, denied.text
    assert allowed.status_code == 200, allowed.text


# ---------------------------------------------------------------------------
# POST /v1/commentary и /v1/chat: доступ проверяется до LLM
# ---------------------------------------------------------------------------


def test_commentary_on_foreign_game_returns_404_before_llm(
    env: IsolationEnv,
) -> None:
    before = asyncio.run(_commentary_count(env.game_a))
    response = env.client.post(
        "/v1/commentary",
        json={"analysis_id": env.game_a, "level": "club"},
        headers=_headers(env.token_b),
    )
    after = asyncio.run(_commentary_count(env.game_a))

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"
    assert after == before, "чужая партия не должна порождать комментарий"


def test_chat_on_foreign_game_returns_404_before_llm(env: IsolationEnv) -> None:
    response = env.client.post(
        "/v1/chat",
        json={
            "analysis_id": env.game_a,
            "level": "club",
            "message": "Что стоило сыграть в дебюте?",
        },
        headers=_headers(env.token_b),
    )

    assert response.status_code == 404, response.text
    assert response.json()["code"] == "not_found"


def test_chat_requires_token(env: IsolationEnv) -> None:
    response = env.client.post(
        "/v1/chat",
        json={
            "analysis_id": env.game_a,
            "level": "club",
            "message": "Что стоило сыграть в дебюте?",
        },
    )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


# ---------------------------------------------------------------------------
# PATCH /v1/commentaries/{id}: доступ по владельцу + edits.user_id
# ---------------------------------------------------------------------------


def test_patch_commentary_is_owner_scoped(env: IsolationEnv) -> None:
    denied = env.client.patch(
        f"/v1/commentaries/{env.commentary_a}",
        json={"text": "Чужая правка."},
        headers=_headers(env.token_b),
    )
    assert denied.status_code == 404, denied.text
    assert denied.json()["code"] == "not_found"

    allowed = env.client.patch(
        f"/v1/commentaries/{env.commentary_a}",
        json={"text": PATCH_TEXT},
        headers=_headers(env.token_a),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["text"] == PATCH_TEXT

    owner = asyncio.run(_latest_edit_owner(env.commentary_a))
    assert owner == env.user_a, "edits.user_id должен быть автором правки"


# ---------------------------------------------------------------------------
# DB helpers (NullPool: отдельный loop на каждый вызов — как в сюите)
# ---------------------------------------------------------------------------


async def _promote_admin(email: str) -> None:
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None, f"нет пользователя {email}"
            user.role = AdminRole.admin
            await session.commit()
    finally:
        await engine.dispose()


async def _seed_game(user_id: int | None) -> int:
    """Партия с сохранёнными ходами; `user_id=None` — legacy-запись."""
    parsed = parse_pgn(SHORT_PGN)
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = Game(
                user_id=user_id,
                pgn=SHORT_PGN,
                analysis_level=Level.club,
                analysis_time_ms=1,
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
                        best_move_san=move.san,
                        delta_cp=0,
                        classification="good",
                        engine_depth=8,
                    )
                    for move in parsed.moves
                ]
            )
            await session.commit()
            return game.id
    finally:
        await engine.dispose()


async def _seed_commentary(game_id: int, user_id: int) -> int:
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            commentary = Commentary(
                game_id=game_id,
                user_id=user_id,
                level=Level.club,
                text="Комментарий пользователя A.",
                model="test-model",
                tokens_in=10,
                tokens_out=20,
                validated=True,
            )
            session.add(commentary)
            await session.commit()
            return commentary.id
    finally:
        await engine.dispose()


async def _game_owner(game_id: int) -> int | None:
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            game = await session.get(Game, game_id)
            return None if game is None else game.user_id
    finally:
        await engine.dispose()


async def _game_ids(user_id: int) -> set[int]:
    engine = _engine()
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                select(Game.id).where(Game.user_id == user_id)
            )
            return {int(row[0]) for row in rows}
    finally:
        await engine.dispose()


async def _commentary_count(game_id: int) -> int:
    engine = _engine()
    try:
        async with engine.connect() as connection:
            total = await connection.scalar(
                select(func.count())
                .select_from(Commentary)
                .where(Commentary.game_id == game_id)
            )
            return int(total or 0)
    finally:
        await engine.dispose()


async def _latest_edit_owner(commentary_id: int) -> int | None:
    engine = _engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            edit = await session.scalar(
                select(Edit)
                .where(Edit.commentary_id == commentary_id)
                .order_by(Edit.id.desc())
                .limit(1)
            )
            return None if edit is None else edit.user_id
    finally:
        await engine.dispose()


async def _cleanup(
    *, user_ids: list[int], emails: list[str], legacy_game: int
) -> None:
    """Удалить seeded-партии и пользователей (партии каскадно чистят детей)."""
    engine = _engine()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(Game).where(
                    (Game.user_id.in_(user_ids)) | (Game.id == legacy_game)
                )
            )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
    finally:
        await engine.dispose()
