"""Интеграционные тесты профиля и смены пароля (T-082).

Контракт:

* `PATCH /auth/me` правит `display_name` и/или `level` аутентифицированного
  пользователя, обрезает пробелы в имени, запрещает пустое/слишком длинное
  имя, неизвестные поля и «пустой» запрос; ответ совпадает с `GET /auth/me`;
* `POST /auth/change-password` проверяет текущий пароль тем же Argon2-
  верификатором, что и вход, отклоняет короткий/совпадающий новый пароль,
  обновляет хеш и глобально отзывает refresh-токены (KI-041-механизм).

Требуются PostgreSQL; тест отзыва refresh дополнительно требует Redis.
Без них соответствующие тесты скипаются.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.denylist import global_revocation_key
from app.auth.passwords import verify_password
from app.config import Settings, get_settings
from app.db.models import User
from app.main import create_app

PASSWORD = "profile-password-123"
NEW_PASSWORD = "profile-password-456"
WRONG_PASSWORD = "definitely-wrong-password"
COOKIE_NAME = "refresh_token"

PROFILE_KEYS = {
    "id",
    "email",
    "display_name",
    "level",
    "role",
    #: T-083: тариф пользователя (free/pro).
    "tier",
    "is_active",
    "is_verified",
    "created_at",
}


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


def _redis_available() -> bool:
    """Проверить доступность Redis (нужен для проверки отзыва refresh)."""

    async def ping() -> None:
        client = aioredis.from_url(
            get_settings().redis_url, decode_responses=True
        )
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        asyncio.run(ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)
requires_redis = pytest.mark.skipif(
    not _redis_available(), reason="Redis недоступен"
)


def _settings() -> Settings:
    """Настройки приложения с реальной (не bypass) JWT-аутентификацией."""
    return get_settings().model_copy(
        update={
            "app_env": "test",
            "test_auth_bypass": False,
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "prompt_pubsub_enabled": False,
            "semantic_cache_enabled": False,
            "engine_cache_enabled": False,
            "otel_enabled": False,
            "app_base_url": "http://testserver",
        }
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


def _email() -> str:
    return f"profile-{uuid4().hex}@example.com"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(
    client: TestClient, email: str, password: str = PASSWORD
) -> dict[str, Any]:
    response = client.post(
        "/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(
    client: TestClient, email: str, password: str = PASSWORD
) -> dict[str, Any]:
    response = client.post(
        "/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _read_user(email: str) -> tuple[str | None, str | None, str]:
    """Прочитать `(password_hash, display_name, level)` из БД."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            return user.password_hash, user.display_name, user.level.value
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


async def _delete_global_revocation(user_id: int) -> None:
    """Убрать маркер `revoked_before:<id>`, созданный тестом."""
    client = aioredis.from_url(
        get_settings().redis_url, decode_responses=True
    )
    try:
        await client.delete(global_revocation_key(user_id))
    finally:
        await client.aclose()


# --- PATCH /auth/me -------------------------------------------------------


def test_patch_level_updates_profile_and_me(client: TestClient) -> None:
    email = _email()
    try:
        profile = _register(client, email)
        assert profile["display_name"] is None
        tokens = _login(client, email)
        headers = _auth_headers(tokens["access_token"])

        response = client.patch(
            "/auth/me", json={"level": "gm"}, headers=headers
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == PROFILE_KEYS
        assert body["level"] == "gm"
        assert body["display_name"] is None
        assert body["id"] == profile["id"]

        me = client.get("/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["level"] == "gm"

        password_hash, display_name, level = asyncio.run(
            _read_user(email)
        )
        assert password_hash is not None
        assert display_name is None
        assert level == "gm"
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_display_name_is_trimmed_and_persisted(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)
        headers = _auth_headers(tokens["access_token"])

        response = client.patch(
            "/auth/me",
            json={"display_name": "  Magnus Carlsen  "},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["display_name"] == "Magnus Carlsen"
        me = client.get("/auth/me", headers=headers)
        assert me.json()["display_name"] == "Magnus Carlsen"
        _, display_name, _ = asyncio.run(_read_user(email))
        assert display_name == "Magnus Carlsen"
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_updates_display_name_and_level_together(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)
        headers = _auth_headers(tokens["access_token"])

        response = client.patch(
            "/auth/me",
            json={"display_name": "  Алиса  ", "level": "beginner"},
            headers=headers,
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["display_name"] == "Алиса"
        assert body["level"] == "beginner"
        assert set(body) == PROFILE_KEYS
    finally:
        asyncio.run(_delete_users([email]))


@pytest.mark.parametrize("level", ["grandmaster", "GM", "", "club "])
def test_patch_rejects_invalid_level(
    client: TestClient, level: str
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.patch(
            "/auth/me",
            json={"level": level},
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
        _, _, stored_level = asyncio.run(_read_user(email))
        assert stored_level == "club"
    finally:
        asyncio.run(_delete_users([email]))


@pytest.mark.parametrize("display_name", ["", "   ", "\t\n"])
def test_patch_rejects_empty_display_name(
    client: TestClient, display_name: str
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.patch(
            "/auth/me",
            json={"display_name": display_name},
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
        _, stored_name, _ = asyncio.run(_read_user(email))
        assert stored_name is None
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_rejects_too_long_display_name(client: TestClient) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.patch(
            "/auth/me",
            json={"display_name": "x" * 121},
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_rejects_unknown_field(client: TestClient) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.patch(
            "/auth/me",
            json={"nickname": "magnus"},
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_without_changes_returns_422(client: TestClient) -> None:
    """Пустое тело и оба поля `null` — 422 (а не ложное «обновлено»)."""
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)
        headers = _auth_headers(tokens["access_token"])

        empty = client.patch("/auth/me", json={}, headers=headers)
        explicit_nulls = client.patch(
            "/auth/me",
            json={"display_name": None, "level": None},
            headers=headers,
        )

        assert empty.status_code == 422, empty.text
        assert explicit_nulls.status_code == 422, explicit_nulls.text
    finally:
        asyncio.run(_delete_users([email]))


def test_patch_requires_authentication(client: TestClient) -> None:
    response = client.patch("/auth/me", json={"level": "gm"})

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


# --- POST /auth/change-password -------------------------------------------


def test_change_password_updates_hash_and_new_password_logs_in(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.post(
            "/auth/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
            },
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 200, response.text
        assert response.json()["detail"]

        old_login = client.post(
            "/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert old_login.status_code == 401, old_login.text
        assert old_login.json()["code"] == "unauthorized"

        _login(client, email, NEW_PASSWORD)
        password_hash, _, _ = asyncio.run(_read_user(email))
        assert password_hash is not None
        assert password_hash.startswith("$argon2id$")
        assert verify_password(NEW_PASSWORD, password_hash) is True
        assert verify_password(PASSWORD, password_hash) is False
    finally:
        asyncio.run(_delete_users([email]))


def test_change_password_with_wrong_current_returns_401(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.post(
            "/auth/change-password",
            json={
                "current_password": WRONG_PASSWORD,
                "new_password": NEW_PASSWORD,
            },
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 401, response.text
        assert response.json()["code"] == "unauthorized"
        # Пароль не изменился: старый по-прежнему пускает.
        _login(client, email, PASSWORD)
    finally:
        asyncio.run(_delete_users([email]))


def test_change_password_rejects_short_new_password(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.post(
            "/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "short"},
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
    finally:
        asyncio.run(_delete_users([email]))


def test_change_password_rejects_same_password(client: TestClient) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)

        response = client.post(
            "/auth/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": PASSWORD,
            },
            headers=_auth_headers(tokens["access_token"]),
        )

        assert response.status_code == 422, response.text
        # Хеш не перезаписан тем же паролем.
        _login(client, email, PASSWORD)
    finally:
        asyncio.run(_delete_users([email]))


def test_change_password_requires_authentication(
    client: TestClient,
) -> None:
    response = client.post(
        "/auth/change-password",
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


@requires_redis
def test_change_password_revokes_existing_refresh_sessions(
    client: TestClient,
) -> None:
    """Смена пароля инвалидирует старые refresh (как reset-password)."""
    email = _email()
    user_id: int | None = None
    try:
        _register(client, email)
        tokens = _login(client, email)
        user_id = tokens["user"]["id"]
        old_refresh = client.cookies.get(COOKIE_NAME)
        assert old_refresh

        response = client.post(
            "/auth/change-password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
            },
            headers=_auth_headers(tokens["access_token"]),
        )
        assert response.status_code == 200, response.text
        # Своя refresh-cookie очищена — токен всё равно отозван.
        assert client.cookies.get(COOKIE_NAME) is None

        client.cookies.clear()
        replay = client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        assert replay.status_code == 401, replay.text
        assert replay.json()["code"] == "unauthorized"

        # Новый вход новым паролем выдаёт рабочий refresh.
        _login(client, email, NEW_PASSWORD)
        refreshed = client.post("/auth/refresh")
        assert refreshed.status_code == 200, refreshed.text
    finally:
        if user_id is not None:
            asyncio.run(_delete_global_revocation(user_id))
        asyncio.run(_delete_users([email]))
