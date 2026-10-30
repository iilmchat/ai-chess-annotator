"""KI-058: локализация `detail` ошибок API по `Accept-Language`.

Проверяем сквозной контракт: русский текст по умолчанию (регрессия), а с
`Accept-Language: en` известные доменные коды отдают английский `detail` из
`app.i18n`, при этом `code` не меняется. Требуется PostgreSQL; без него
модуль скипается.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.db.models import AdminRole, User
from app.i18n import ERROR_MESSAGES
from app.main import create_app

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


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="PostgreSQL недоступен"
)


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
        }
    )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(create_app(_settings())) as c:
        yield c


async def _insert_user(*, email: str, is_verified: bool) -> User:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = User(
                email=email,
                role=AdminRole.user,
                is_active=True,
                is_verified=is_verified,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user
    finally:
        await engine.dispose()


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


def _bearer(user: User) -> dict[str, str]:
    token = create_access_token(user, _settings())
    return {"Authorization": f"Bearer {token}"}


def test_unverified_user_error_localized_to_english(
    client: TestClient,
) -> None:
    email = f"i18n-unverified-{uuid4().hex}@example.com"
    try:
        user = asyncio.run(_insert_user(email=email, is_verified=False))
        english = client.post(
            "/v1/analyze",
            json={"pgn": SHORT_PGN, "level": "club"},
            headers={
                **_bearer(user),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        assert english.status_code == 403, english.text
        body = english.json()
        assert body["code"] == "email_not_verified"
        assert body["detail"] == ERROR_MESSAGES["email_not_verified"]["en"]

        russian = client.post(
            "/v1/analyze",
            json={"pgn": SHORT_PGN, "level": "club"},
            headers=_bearer(user),
        )
        assert russian.status_code == 403, russian.text
        assert russian.json()["code"] == "email_not_verified"
        assert (
            russian.json()["detail"]
            == "Подтвердите email, чтобы пользоваться анализом и комментариями"
        )
    finally:
        asyncio.run(_delete_users([email]))


def test_unauthorized_error_localized_by_header(client: TestClient) -> None:
    english = client.get(
        "/v1/commentaries/1/export/pdf",
        headers={"Accept-Language": "en"},
    )
    assert english.status_code == 401
    assert english.json()["code"] == "unauthorized"
    assert english.json()["detail"] == ERROR_MESSAGES["unauthorized"]["en"]

    russian = client.get(
        "/v1/commentaries/1/export/pdf",
        headers={"Accept-Language": "ru-RU,ru;q=0.9"},
    )
    assert russian.status_code == 401
    assert russian.json()["code"] == "unauthorized"
    assert "Требуется авторизация" in russian.json()["detail"]


def test_not_found_error_localized_to_english(client: TestClient) -> None:
    email = f"i18n-verified-{uuid4().hex}@example.com"
    try:
        user = asyncio.run(_insert_user(email=email, is_verified=True))
        response = client.get(
            "/v1/commentaries/999999999/export/pdf",
            headers={
                **_bearer(user),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

        assert response.status_code == 404, response.text
        body: dict[str, Any] = response.json()
        assert body["code"] == "not_found"
        assert body["detail"] == ERROR_MESSAGES["not_found"]["en"]
    finally:
        asyncio.run(_delete_users([email]))
