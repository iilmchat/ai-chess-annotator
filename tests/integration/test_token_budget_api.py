"""Проводка TokenBudgetMiddleware в реальном приложении (T-031, KI-038).

Бюджет считается по JWT-пользователю (`usage:{day}:user:{id}`), а не по IP:
два пользователя с одного IP имеют независимые счётчики. Лимит — 5 токенов
(free и pro одинаковы), поэтому 429 проверяется без Stockfish/LM Studio:
middleware срабатывает раньше эндпоинта. Запрос «свободного» пользователя
доходит до эндпоинта и получает 400 `invalid_pgn` — это доказывает, что
middleware его пропустил.

Требует PostgreSQL (пользователи создаются через `/auth/register`) и Redis:
и то, и другое при недоступности — `skip`, а не красный тест.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.models import User
from app.finops import utc_day
from app.main import create_app

PASSWORD = "finops-budget-password-123"
#: Дневной лимит и для free, и для pro — так 429 воспроизводим без LLM.
LIMIT = 5
#: IP, который Starlette TestClient кладёт в scope (анонимный субъект).
TESTCLIENT_IP = "testclient"
#: Заведомо невалидный PGN: эндпоинт отвечает 400 раньше движка.
INVALID_PGN = "not a chess game"


def _settings() -> Settings:
    """Реальная авторизация, бюджет включён, лимиты одинаковые."""
    return Settings(
        _env_file=None,
        app_env="test",
        test_auth_bypass=False,
        token_budget_enabled=True,
        daily_token_limit_free=LIMIT,
        daily_token_limit_pro=LIMIT,
        pro_ips="",
        trust_forwarded_for=False,
        rate_limit_enabled=False,
        llm_warmup_enabled=False,
        prompt_pubsub_enabled=False,
        semantic_cache_enabled=False,
        otel_enabled=False,
    )


async def _db_ping() -> None:
    engine = create_async_engine(_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def _db_available() -> bool:
    try:
        asyncio.run(_db_ping())
    except Exception:
        return False
    return True


async def _redis_ping() -> None:
    client = aioredis.from_url(_settings().redis_url, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.aclose()


def _redis_available() -> bool:
    try:
        asyncio.run(_redis_ping())
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not (_db_available() and _redis_available()),
    reason="PostgreSQL/Redis недоступны",
)


def _usage_key(subject: str) -> str:
    """Тот же ключ, что строит `TokenUsageTracker`."""
    return f"usage:{utc_day()}:{subject}"


async def _seed_usage(entries: dict[str, int]) -> None:
    client = aioredis.from_url(_settings().redis_url, decode_responses=True)
    try:
        for key, value in entries.items():
            await client.set(key, value, ex=3600)
    finally:
        await client.aclose()


async def _drop_usage(keys: list[str]) -> None:
    client = aioredis.from_url(_settings().redis_url, decode_responses=True)
    try:
        if keys:
            await client.delete(*keys)
    finally:
        await client.aclose()


async def _delete_users(emails: list[str]) -> None:
    engine = create_async_engine(_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(delete(User).where(User.email.in_(emails)))
            await session.commit()
    finally:
        await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(client: TestClient, email: str) -> int:
    response = client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _verify_email(client: TestClient, email: str) -> None:
    """KI-041: подтвердить email (иначе /v1/analyze вернёт 403)."""
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
class BudgetEnv:
    client: TestClient
    user_a_id: int
    user_b_id: int
    token_a: str
    token_b: str


@pytest.fixture(scope="module")
def env() -> Iterator[BudgetEnv]:
    suffix = uuid.uuid4().hex
    email_a = f"finops-a-{suffix}@example.com"
    email_b = f"finops-b-{suffix}@example.com"
    emails = [email_a, email_b]
    with TestClient(create_app(_settings())) as client:
        user_a_id = _register(client, email_a)
        user_b_id = _register(client, email_b)
        _verify_email(client, email_a)
        _verify_email(client, email_b)
        token_a = _login(client, email_a)
        token_b = _login(client, email_b)
        try:
            yield BudgetEnv(
                client=client,
                user_a_id=user_a_id,
                user_b_id=user_b_id,
                token_a=token_a,
                token_b=token_b,
            )
        finally:
            asyncio.run(
                _drop_usage(
                    [
                        _usage_key(f"user:{user_a_id}"),
                        _usage_key(f"user:{user_b_id}"),
                        _usage_key(f"ip:{TESTCLIENT_IP}"),
                    ]
                )
            )
            asyncio.run(_delete_users(emails))


def test_user_is_blocked_by_own_user_counter(env: BudgetEnv) -> None:
    """Исчерпан счётчик `user:<A>` → 429, даже если IP «чистый»."""
    key = _usage_key(f"user:{env.user_a_id}")
    asyncio.run(_seed_usage({key: LIMIT}))
    try:
        response = env.client.post(
            "/v1/analyze",
            json={"pgn": "1. e4 e5", "level": "club"},
            headers=_headers(env.token_a),
        )
    finally:
        asyncio.run(_drop_usage([key]))

    assert response.status_code == 429, response.text
    assert response.json()["code"] == "token_budget_exceeded"
    assert response.headers["X-RateLimit-Limit"] == str(LIMIT)
    assert response.headers["X-RateLimit-Remaining"] == "0"
    assert response.headers["X-Token-Budget-Period"] == "day"


def test_second_user_on_same_ip_is_not_blocked(env: BudgetEnv) -> None:
    """Тот же IP, но свой user-счётчик: middleware пропускает к эндпоинту."""
    key_a = _usage_key(f"user:{env.user_a_id}")
    asyncio.run(_seed_usage({key_a: LIMIT}))
    try:
        response = env.client.post(
            "/v1/analyze",
            json={"pgn": INVALID_PGN, "level": "club"},
            headers=_headers(env.token_b),
        )
    finally:
        asyncio.run(_drop_usage([key_a]))

    # 400 — уже ответ эндпоинта (невалидный PGN), бюджет B не тронут.
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "invalid_pgn"


def test_anonymous_request_is_blocked_by_ip_counter(env: BudgetEnv) -> None:
    key = _usage_key(f"ip:{TESTCLIENT_IP}")
    asyncio.run(_seed_usage({key: LIMIT}))
    try:
        response = env.client.post(
            "/v1/analyze", json={"pgn": "1. e4 e5", "level": "club"}
        )
    finally:
        asyncio.run(_drop_usage([key]))

    assert response.status_code == 429, response.text
    assert response.json()["code"] == "token_budget_exceeded"


def test_usage_endpoint_reports_user_counter(env: BudgetEnv) -> None:
    key = _usage_key(f"user:{env.user_a_id}")
    asyncio.run(_seed_usage({key: LIMIT}))
    try:
        response = env.client.get(
            "/v1/users/me/usage", headers=_headers(env.token_a)
        )
    finally:
        asyncio.run(_drop_usage([key]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "free"
    assert body["used_tokens"] == LIMIT
    assert body["limit_tokens"] == LIMIT
    assert body["remaining_tokens"] == 0
    assert body["enabled"] is True


def test_usage_endpoint_anonymous_uses_ip_counter(env: BudgetEnv) -> None:
    key = _usage_key(f"ip:{TESTCLIENT_IP}")
    asyncio.run(_seed_usage({key: 2}))
    try:
        response = env.client.get("/v1/users/me/usage")
    finally:
        asyncio.run(_drop_usage([key]))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tier"] == "free"
    assert body["used_tokens"] == 2
    assert body["enabled"] is True
