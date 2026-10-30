"""Тариф и дневной лимит токенов в `GET /v1/users/me/usage` (T-028, T-083).

Приложение собирается с реальной аутентификацией (`test_auth_bypass=False`),
бюджет токенов выключен — поэтому эндпоинт отдаёт `_fallback` с лимитом
соответствующего тира. С итерации 40 `subject` резолвится из JWT, а `tier`
читается из `users.tier` (а не из роли/PRO_IPS). Проверяется:

* аноним (без токена) → `free` по IP;
* обычный пользователь → `free`;
* пользователь с `tier='pro'` → `pro`, даже с ролью `user`;
* администратор с `tier='free'` → `free` (роль больше не даёт Pro);
* битый токен → анонимный ответ по IP, а не 401.

Тесты требуют PostgreSQL: пользователи создаются через `/auth/register`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings, get_settings
from app.db.models import AdminRole, User
from app.main import create_app

PASSWORD = "usage-roles-password-123"


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
    """Реальная авторизация, бюджет выключен, IP-белый список пуст."""
    return Settings(
        _env_file=None,
        app_env="test",
        test_auth_bypass=False,
        rate_limit_enabled=False,
        token_budget_enabled=False,
        llm_warmup_enabled=False,
        prompt_pubsub_enabled=False,
        semantic_cache_enabled=False,
        pro_ips="",
        jwt_secret="usage-roles-test-secret-0123456789-0123456789",
    )


def _register(client: TestClient, email: str) -> None:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _set_role(email: str, role: AdminRole) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None, f"нет пользователя {email}"
            user.role = role
            await session.commit()
    finally:
        await engine.dispose()


async def _set_tier(email: str, tier: str) -> None:
    """T-083: тариф — источник истины, меняется напрямую в БД (как webhook)."""
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email == email))
            assert user is not None, f"нет пользователя {email}"
            user.tier = tier
            await session.commit()
    finally:
        await engine.dispose()


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
    finally:
        await engine.dispose()


@dataclass(frozen=True, slots=True)
class UsageEnv:
    client: TestClient
    user_token: str
    admin_token: str
    pro_token: str


@pytest.fixture(scope="module")
def env() -> Iterator[UsageEnv]:
    suffix = uuid.uuid4().hex
    email_user = f"usage-user-{suffix}@example.com"
    email_admin = f"usage-admin-{suffix}@example.com"
    email_pro = f"usage-pro-{suffix}@example.com"
    emails = [email_user, email_admin, email_pro]

    with TestClient(create_app(_settings())) as client:
        for email in emails:
            _register(client, email)
        # Роль admin сама по себе больше не даёт Pro (T-083).
        asyncio.run(_set_role(email_admin, AdminRole.admin))
        asyncio.run(_set_tier(email_pro, "pro"))
        user_token = _login(client, email_user)
        admin_token = _login(client, email_admin)
        pro_token = _login(client, email_pro)
        try:
            yield UsageEnv(
                client=client,
                user_token=user_token,
                admin_token=admin_token,
                pro_token=pro_token,
            )
        finally:
            asyncio.run(_delete_users(emails))


def test_anonymous_usage_is_free_tier(env: UsageEnv) -> None:
    response = env.client.get("/v1/users/me/usage")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "free"
    assert body["limit_tokens"] == 50_000
    assert body["enabled"] is False


def test_regular_user_usage_is_free_tier(env: UsageEnv) -> None:
    response = env.client.get(
        "/v1/users/me/usage", headers=_headers(env.user_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "free"
    assert body["limit_tokens"] == 50_000


def test_admin_role_without_pro_tier_is_free(env: UsageEnv) -> None:
    """T-083: роль admin больше не выдаёт Pro — только `users.tier`."""
    response = env.client.get(
        "/v1/users/me/usage", headers=_headers(env.admin_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "free"
    assert body["limit_tokens"] == 50_000


def test_user_tier_pro_gets_pro_limit(env: UsageEnv) -> None:
    """Обычный пользователь с тарифом pro получает 500k."""
    response = env.client.get(
        "/v1/users/me/usage", headers=_headers(env.pro_token)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "pro"
    assert body["limit_tokens"] == 500_000


def test_usage_accepts_invalid_token_as_anonymous(env: UsageEnv) -> None:
    """Битый токен не даёт 401: резолвер FinOps откатывается на IP (KI-038)."""
    response = env.client.get(
        "/v1/users/me/usage", headers=_headers("not-a-valid-jwt")
    )

    assert response.status_code == 200, response.text
    assert response.json()["tier"] == "free"
