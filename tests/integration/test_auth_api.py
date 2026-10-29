"""Интеграционные тесты `/auth` с реальной JWT-аутентификацией (T-028).

Старая сюита ходит в API анонимно через тестовый seam
(`APP_ENV=test` + `TEST_AUTH_BYPASS=true`). Здесь наоборот: приложение
собирается с `test_auth_bypass=False`, поэтому зависимости
`get_current_user`/`get_current_user_optional` не подменяются и проверяется
настоящая цепочка «регистрация → вход → токены → профиль».

Тесты требуют PostgreSQL (как и остальные интеграционные), импортируемая
схема — миграция `c7d8e9f0a1b2`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import User
from app.main import create_app

#: Пароль, удовлетворяющий схеме (8..72 символа / байта).
PASSWORD = "auth-api-password-123"
#: Значение по умолчанию `access_token_ttl_min` (15 минут) в секундах.
ACCESS_TTL_S = 15 * 60

PROFILE_KEYS = {"id", "email", "level", "role", "is_active", "created_at"}
TOKEN_PAIR_KEYS = {
    "access_token",
    "refresh_token",
    "token_type",
    "expires_in",
    "user",
}


def _db_available() -> bool:
    """Проверить доступность PostgreSQL (как в остальных интеграционных тестах)."""

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


pytestmark = pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен")


def _settings() -> Settings:
    """Настройки приложения с ВЫКЛЮЧЕННЫМ тестовым bypass-ом авторизации."""
    return Settings(
        _env_file=None,
        app_env="test",
        test_auth_bypass=False,
        rate_limit_enabled=False,
        token_budget_enabled=False,
        llm_warmup_enabled=False,
        prompt_pubsub_enabled=False,
        semantic_cache_enabled=False,
    )


def _client() -> TestClient:
    """TestClient с реальной аутентификацией."""
    return TestClient(create_app(_settings()))


def _email() -> str:
    """Уникальный email: таблица `users` общая, пустой она не считается."""
    return f"auth-api-{uuid4().hex}@example.com"


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


async def _set_active(email: str, *, active: bool) -> None:
    """Переключить `is_active` напрямую в БД (сценарий «отключённый user»)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            user.is_active = active
            await session.commit()
    finally:
        await engine.dispose()


async def _read_user(email: str) -> tuple[str | None, bool, datetime | None]:
    """Прочитать `(password_hash, is_active, last_login_at)` из БД."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None, f"нет пользователя {email}"
            return user.password_hash, user.is_active, user.last_login_at
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    """Убрать созданных тестами пользователей (партии у них не создаются)."""
    if not emails:
        return
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


# --- Регистрация ----------------------------------------------------------


def test_register_returns_profile_and_lowercases_email() -> None:
    email = f"Auth-{uuid4().hex}@Example.COM"
    try:
        with _client() as client:
            response = client.post(
                "/auth/register",
                json={"email": email, "password": PASSWORD},
            )

        assert response.status_code == 201, response.text
        body = response.json()
        assert set(body) == PROFILE_KEYS
        assert body["id"] > 0
        assert body["email"] == email.lower()
        assert body["level"] == "club"
        assert body["role"] == "user"
        assert body["is_active"] is True
        assert datetime.fromisoformat(body["created_at"]) is not None
    finally:
        asyncio.run(_delete_users([email.lower()]))


def test_register_duplicate_email_returns_409() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            # Другой регистр — тот же email после нормализации.
            response = client.post(
                "/auth/register",
                json={"email": email.upper(), "password": PASSWORD},
            )

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "email_taken"
        assert body["detail"]
    finally:
        asyncio.run(_delete_users([email]))


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": PASSWORD},
        {"email": "short-password@example.com", "password": "short"},
        {"email": "long-password@example.com", "password": "a" * 73},
        {"email": "multibyte@example.com", "password": "я" * 40},
    ],
)
def test_register_rejects_invalid_input(payload: dict[str, str]) -> None:
    with _client() as client:
        response = client.post("/auth/register", json=payload)

    assert response.status_code == 422, response.text


# --- Вход -----------------------------------------------------------------


def test_login_returns_token_pair_and_updates_last_login() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            response = client.post(
                "/auth/login",
                json={"email": email.upper(), "password": PASSWORD},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == TOKEN_PAIR_KEYS
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["access_token"] != body["refresh_token"]
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == ACCESS_TTL_S
        assert set(body["user"]) == PROFILE_KEYS
        assert body["user"]["email"] == email
        assert body["user"]["role"] == "user"

        password_hash, is_active, last_login_at = asyncio.run(
            _read_user(email)
        )
        assert password_hash is not None
        assert password_hash.startswith("$2")
        assert PASSWORD not in password_hash
        assert is_active is True
        assert last_login_at is not None
    finally:
        asyncio.run(_delete_users([email]))


def test_login_with_wrong_password_returns_401() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            response = client.post(
                "/auth/login",
                json={"email": email, "password": "definitely-wrong"},
            )

        assert response.status_code == 401, response.text
        body = response.json()
        assert body["code"] == "unauthorized"
        assert body["detail"] == "Неверный email или пароль"
    finally:
        asyncio.run(_delete_users([email]))


def test_login_with_unknown_email_returns_401() -> None:
    with _client() as client:
        response = client.post(
            "/auth/login",
            json={"email": _email(), "password": PASSWORD},
        )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"


def test_login_of_inactive_user_returns_403() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
        asyncio.run(_set_active(email, active=False))
        with _client() as client:
            response = client.post(
                "/auth/login",
                json={"email": email, "password": PASSWORD},
            )

        assert response.status_code == 403, response.text
        assert response.json()["code"] == "forbidden"
    finally:
        asyncio.run(_delete_users([email]))


# --- Профиль --------------------------------------------------------------


def test_me_returns_profile_for_access_token() -> None:
    email = _email()
    try:
        with _client() as client:
            profile = _register(client, email)
            tokens = _login(client, email)
            response = client.get(
                "/auth/me",
                headers=_auth_headers(tokens["access_token"]),
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == PROFILE_KEYS
        assert body["id"] == profile["id"]
        assert body["email"] == email
        assert body["role"] == "user"
        assert body["is_active"] is True
    finally:
        asyncio.run(_delete_users([email]))


def test_me_rejects_missing_invalid_and_refresh_tokens() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            tokens = _login(client, email)
            missing = client.get("/auth/me")
            garbage = client.get(
                "/auth/me", headers=_auth_headers("not-a-jwt")
            )
            with_refresh = client.get(
                "/auth/me",
                headers=_auth_headers(tokens["refresh_token"]),
            )
            wrong_scheme = client.get(
                "/auth/me",
                headers={"Authorization": tokens["access_token"]},
            )

        for response in (missing, garbage, with_refresh, wrong_scheme):
            assert response.status_code == 401, response.text
            assert response.json()["code"] == "unauthorized"
    finally:
        asyncio.run(_delete_users([email]))


# --- Обновление токенов ---------------------------------------------------


def test_refresh_returns_rotated_pair() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            first = _login(client, email)
            response = client.post(
                "/auth/refresh",
                json={"refresh_token": first["refresh_token"]},
            )

            assert response.status_code == 200, response.text
            rotated = response.json()
            assert set(rotated) == TOKEN_PAIR_KEYS
            assert rotated["access_token"] != first["access_token"]
            assert rotated["refresh_token"] != first["refresh_token"]
            assert rotated["token_type"] == "bearer"
            assert rotated["expires_in"] == ACCESS_TTL_S
            assert rotated["user"]["email"] == email

            # Новая пара рабочая: access пускает в профиль...
            me = client.get(
                "/auth/me", headers=_auth_headers(rotated["access_token"])
            )
            # ...а новый refresh можно ротировать ещё раз.
            again = client.post(
                "/auth/refresh",
                json={"refresh_token": rotated["refresh_token"]},
            )

        assert me.status_code == 200, me.text
        assert me.json()["email"] == email
        assert again.status_code == 200, again.text
        assert again.json()["access_token"] != rotated["access_token"]
    finally:
        asyncio.run(_delete_users([email]))


def test_refresh_rejects_access_token() -> None:
    email = _email()
    try:
        with _client() as client:
            _register(client, email)
            tokens = _login(client, email)
            response = client.post(
                "/auth/refresh",
                json={"refresh_token": tokens["access_token"]},
            )

        assert response.status_code == 401, response.text
        assert response.json()["code"] == "unauthorized"
    finally:
        asyncio.run(_delete_users([email]))


def test_refresh_rejects_malformed_token() -> None:
    with _client() as client:
        response = client.post(
            "/auth/refresh", json={"refresh_token": "not-a-jwt"}
        )

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "unauthorized"
