"""KI-041: верификация email, сброс пароля и logout-all.

Проверяем контракт:

* регистрация → `is_verified=False`, письмо со ссылкой в `MockEmailSender`;
* `/v1/analyze` и `/v1/commentary` → 403 `email_not_verified` до перехода
  по ссылке; `GET /auth/verify-email` выставляет флаг (идемпотентно);
* `POST /auth/forgot-password` не раскрывает существование email, а
  `POST /auth/reset-password` меняет хеш, отзывает старые refresh и
  делает токен одноразовым;
* `POST /auth/logout-all` инвалидирует refresh-токены, выпущенные до
  вызова, но не мешает новому входу.

Требуются PostgreSQL и Redis; без них модуль скипается.
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

from app.config import Settings, get_settings
from app.db.models import User
from app.main import create_app

PASSWORD = "OldPassword123!"
NEW_PASSWORD = "NewPassword456!"
COOKIE_NAME = "refresh_token"
SHORT_PGN = "1. e4 e5 2. Nf3 Nc6 1/2-1/2"


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


def _redis_available() -> bool:
    async def ping() -> None:
        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            get_settings().redis_url
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


pytestmark = [
    pytest.mark.skipif(not _db_available(), reason="PostgreSQL недоступен"),
    pytest.mark.skipif(not _redis_available(), reason="Redis недоступен"),
]


def _settings() -> Settings:
    return get_settings().model_copy(
        update={
            "app_env": "test",
            "test_auth_bypass": False,
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "engine_cache_enabled": False,
            "otel_enabled": False,
            "app_base_url": "http://testserver",
        }
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                User.__table__.delete().where(User.email.in_(emails))
            )
    finally:
        await engine.dispose()


async def _read_user(email: str) -> User:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(
                select(User).where(User.email == email)
            )
            assert user is not None
            return user
    finally:
        await engine.dispose()


def _email() -> str:
    return f"ki041-{uuid4().hex}@example.com"


def _register(client: TestClient, email: str) -> dict[str, Any]:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
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


def _bearer(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _outbox(client: TestClient) -> list[Any]:
    sender = client.app.state.email_sender
    return list(sender.outbox)


def _last_token(client: TestClient, email: str, kind: str) -> str:
    messages = [
        message
        for message in _outbox(client)
        if message.to == email and message.kind == kind
    ]
    assert messages, f"нет письма {kind} для {email}"
    return str(messages[-1].token)


def _verify(client: TestClient, email: str) -> None:
    token = _last_token(client, email, "verify_email")
    response = client.get("/auth/verify-email", params={"token": token})
    assert response.status_code == 200, response.text
    assert response.json()["is_verified"] is True


# --- Email verification ---------------------------------------------------


def test_register_creates_unverified_user_and_sends_link(
    client: TestClient,
) -> None:
    email = _email()
    try:
        profile = _register(client, email)
        assert profile["is_verified"] is False
        message = _outbox(client)[-1]
        assert message.kind == "verify_email"
        assert message.to == email
        assert message.token
        assert "/auth/verify-email?token=" in message.link
    finally:
        asyncio.run(_delete_users([email]))


def test_unverified_user_cannot_use_analysis_or_commentary(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)
        headers = _bearer(tokens["access_token"])

        analyze = client.post(
            "/v1/analyze",
            json={"pgn": SHORT_PGN, "level": "club"},
            headers=headers,
        )
        assert analyze.status_code == 403, analyze.text
        assert analyze.json()["code"] == "email_not_verified"

        commentary = client.post(
            "/v1/commentary",
            json={"analysis_id": 1, "level": "club"},
            headers=headers,
        )
        assert commentary.status_code == 403, commentary.text
        assert commentary.json()["code"] == "email_not_verified"

        # После подтверждения зависимость пропускает запрос дальше: для
        # несуществующего analysis_id это уже 404, а не 403.
        _verify(client, email)
        allowed = client.post(
            "/v1/commentary",
            json={"analysis_id": 999999999, "level": "club"},
            headers=headers,
        )
        assert allowed.status_code == 404, allowed.text
    finally:
        asyncio.run(_delete_users([email]))


def test_verify_email_sets_flag_and_is_idempotent(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        token = _last_token(client, email, "verify_email")
        first = client.get("/auth/verify-email", params={"token": token})
        assert first.status_code == 200
        assert first.json()["is_verified"] is True
        second = client.get("/auth/verify-email", params={"token": token})
        assert second.status_code == 200
        tokens = _login(client, email)
        me = client.get(
            "/auth/me", headers=_bearer(tokens["access_token"])
        )
        assert me.status_code == 200
        assert me.json()["is_verified"] is True
        assert asyncio.run(_read_user(email)).is_verified is True
    finally:
        asyncio.run(_delete_users([email]))


def test_verify_email_rejects_invalid_and_wrong_type_tokens(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        tokens = _login(client, email)
        invalid = client.get(
            "/auth/verify-email", params={"token": "not-a-jwt"}
        )
        assert invalid.status_code == 401
        # Access-токен не должен подходить как email_verify.
        wrong = client.get(
            "/auth/verify-email",
            params={"token": tokens["access_token"]},
        )
        assert wrong.status_code == 401
    finally:
        asyncio.run(_delete_users([email]))


# --- Password reset -------------------------------------------------------


def test_forgot_password_is_enumeration_safe(
    client: TestClient,
) -> None:
    known = _email()
    unknown = _email()
    try:
        _register(client, known)
        before = len(_outbox(client))
        known_response = client.post(
            "/auth/forgot-password", json={"email": known}
        )
        unknown_response = client.post(
            "/auth/forgot-password", json={"email": unknown}
        )
        assert known_response.status_code == 202
        assert unknown_response.status_code == 202
        assert known_response.json() == unknown_response.json()
        assert len(_outbox(client)) == before + 1
        message = _outbox(client)[-1]
        assert message.to == known
        assert message.kind == "password_reset"
        assert "/auth/reset-password?token=" in message.link
    finally:
        asyncio.run(_delete_users([known]))


def test_reset_password_changes_hash_revokes_refresh_and_is_single_use(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        _verify(client, email)
        _login(client, email)
        old_refresh = client.cookies.get(COOKIE_NAME)
        assert old_refresh

        forgot = client.post(
            "/auth/forgot-password", json={"email": email}
        )
        assert forgot.status_code == 202
        reset_token = _last_token(client, email, "password_reset")

        reset = client.post(
            "/auth/reset-password",
            json={"token": reset_token, "new_password": NEW_PASSWORD},
        )
        assert reset.status_code == 200, reset.text

        # Старый пароль больше не работает, новый — работает.
        old_login = client.post(
            "/auth/login", json={"email": email, "password": PASSWORD}
        )
        assert old_login.status_code == 401
        _login(client, email, NEW_PASSWORD)

        # Старый refresh отозван глобально (logout-all-механизм).
        # Чистим cookie: иначе refresh возьмёт новый токен из cookie, а не
        # переданный в теле старый.
        client.cookies.clear()
        refresh = client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        assert refresh.status_code == 401

        # Токен сброса одноразовый.
        reused = client.post(
            "/auth/reset-password",
            json={"token": reset_token, "new_password": "AnotherPass789!"},
        )
        assert reused.status_code == 401
    finally:
        asyncio.run(_delete_users([email]))


# --- Logout all -----------------------------------------------------------


def test_logout_all_revokes_old_refresh_but_allows_new_login(
    client: TestClient,
) -> None:
    email = _email()
    try:
        _register(client, email)
        _verify(client, email)
        tokens = _login(client, email)
        old_refresh = client.cookies.get(COOKIE_NAME)
        assert old_refresh

        response = client.post(
            "/auth/logout-all",
            headers=_bearer(tokens["access_token"]),
        )
        assert response.status_code == 204, response.text

        old = client.post(
            "/auth/refresh", json={"refresh_token": old_refresh}
        )
        assert old.status_code == 401

        # Новый вход после logout-all выдаёт валидный refresh.
        _login(client, email)
        new_refresh = client.cookies.get(COOKIE_NAME)
        assert new_refresh and new_refresh != old_refresh
        refreshed = client.post(
            "/auth/refresh", json={"refresh_token": new_refresh}
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"]
    finally:
        asyncio.run(_delete_users([email]))


def test_logout_all_requires_access_token(client: TestClient) -> None:
    response = client.post("/auth/logout-all")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"
