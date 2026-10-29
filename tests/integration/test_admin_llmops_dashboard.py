"""LLMOps-дашборд в админке (T-109) на живой БД.

Проверяем:

* SQL-агрегаты `collect_dashboard`: окна 24h/7d/30d, latency p50/p95/p99
  только по реальным вызовам (без cache-hit), error rate, cache hit rate,
  токены/стоимость и разбивки по модели/уровню;
* виджет «Топ-5 пользователей» из `usage_daily`;
* пустая БД — нули и пустые разбивки, без деления на ноль;
* страница `/admin/llmops`: 200 для admin/auditor, 403 для `user`,
  401 без креденшелов; на странице есть сводка и данные.

Таблицы `llm_calls`/`usage_daily` тест очищает перед сидом и после теста,
поэтому цифры детерминированы и не зависят от других тестов. Требуется
PostgreSQL; без него модуль скипается. Stockfish/LLM не нужны.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.tokens import create_access_token
from app.config import Settings, get_settings
from app.db.models import (
    AdminRole,
    Level,
    LlmCall,
    UsageDaily,
    User,
)
from app.llmops_dashboard import collect_dashboard
from app.main import create_app

ADMIN_TOKEN = "test-admin-token"
ADMIN_PASSWORD = "test-admin-password"
ROLES = ("admin", "auditor", "user")


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


def _db_url() -> str:
    return get_settings().database_url


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    return async_sessionmaker(engine, expire_on_commit=False)


def _llm_call(
    *,
    created_at: datetime,
    model: str,
    level: Level | None,
    status: str,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    cache_hit: bool = False,
) -> LlmCall:
    """Строка `llm_calls` без PII — ровно те поля, что нужны дашборду."""
    return LlmCall(
        trace_id=uuid4(),
        span_id=uuid4().hex[:16],
        operation="chat",
        provider="cache" if cache_hit else "test",
        model=model,
        level=level,
        latency_ms=latency_ms,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=Decimal(str(cost_usd)),
        cache_hit=cache_hit,
        status=status,
        created_at=created_at,
    )


async def _clear_tables() -> None:
    """Удалить все строки `llm_calls`/`usage_daily` (они не append-only)."""
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(delete(LlmCall))
            await connection.execute(delete(UsageDaily))
    finally:
        await engine.dispose()


async def _reset_and_seed() -> dict[str, Any]:
    """Очистить таблицы и засеять детерминированный набор метрик."""
    await _clear_tables()
    now = datetime.now(UTC)
    today = now.date()
    users_rows = [
        ("user", "42", today, 100, 50, Decimal("0.5"), 2),
        ("user", "7", today - timedelta(days=2), 20, 10, Decimal("0.1"), 1),
        (
            "ip",
            "1.2.3.4",
            today - timedelta(days=10),
            300,
            200,
            Decimal("0.2"),
            3,
        ),
        ("user", "99", today - timedelta(days=40), 1000, 1000, Decimal("10"), 1),
    ]
    call_rows = [
        _llm_call(
            created_at=now - timedelta(minutes=30),
            model="model-a",
            level=Level.club,
            status="ok",
            latency_ms=100,
            tokens_in=10,
            tokens_out=5,
            cost_usd=0.001,
        ),
        _llm_call(
            created_at=now - timedelta(minutes=30),
            model="model-b",
            level=Level.gm,
            status="error",
            latency_ms=900,
        ),
        _llm_call(
            created_at=now - timedelta(minutes=30),
            model="semantic-cache",
            level=Level.club,
            status="ok",
            latency_ms=2,
            cache_hit=True,
        ),
        _llm_call(
            created_at=now - timedelta(days=2),
            model="model-a",
            level=Level.club,
            status="ok",
            latency_ms=300,
            tokens_in=20,
            tokens_out=10,
            cost_usd=0.002,
        ),
        _llm_call(
            created_at=now - timedelta(days=10),
            model="model-b",
            level=Level.gm,
            status="timeout",
            latency_ms=800,
            tokens_in=5,
            tokens_out=5,
            cost_usd=0.003,
        ),
        # Вне всех окон дашборда (30 дней) — не должен попасть в метрики.
        _llm_call(
            created_at=now - timedelta(days=40),
            model="model-c",
            level=Level.club,
            status="ok",
            latency_ms=5000,
            tokens_in=1,
            tokens_out=1,
        ),
    ]
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for (
                subject_type,
                subject_key,
                day,
                tin,
                tout,
                cost,
                requests,
            ) in users_rows:
                session.add(
                    UsageDaily(
                        subject_type=subject_type,
                        subject_key=subject_key,
                        day=day,
                        tokens_in=tin,
                        tokens_out=tout,
                        cost_usd=cost,
                        requests=requests,
                    )
                )
            session.add_all(call_rows)
            await session.commit()
    finally:
        await engine.dispose()
    return {"today": today}


async def _cleanup() -> None:
    await _clear_tables()


@pytest.fixture()
def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "admin_api_key": ADMIN_TOKEN,
            "admin_password": ADMIN_PASSWORD,
            "admin_username": "admin",
            "rate_limit_enabled": False,
            "token_budget_enabled": False,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
        }
    )


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings), follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def seeded(client: TestClient) -> Iterator[dict[str, Any]]:
    data = asyncio.run(_reset_and_seed())
    try:
        yield data
    finally:
        asyncio.run(_cleanup())


async def _insert_users(emails: dict[str, str]) -> None:
    engine = create_async_engine(_db_url(), poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for role, email in emails.items():
                session.add(
                    User(email=email, role=AdminRole(role), is_active=True)
                )
            await session.commit()
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


@pytest.fixture()
def users(client: TestClient) -> Iterator[dict[str, str]]:
    emails = {
        role: f"llmops-{role}-{uuid4().hex}@example.com" for role in ROLES
    }
    asyncio.run(_insert_users(emails))
    try:
        yield emails
    finally:
        asyncio.run(_delete_users(list(emails.values())))


def _bearer(
    settings: Settings, users: dict[str, str], role: str
) -> dict[str, str]:
    async def load() -> User:
        engine = create_async_engine(_db_url(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                user = await session.scalar(
                    select(User).where(User.email == users[role])
                )
                assert user is not None
                return user
        finally:
            await engine.dispose()

    user = asyncio.run(load())
    token = create_access_token(user, settings)
    return {"Authorization": f"Bearer {token}"}


# --- Агрегаты -------------------------------------------------------------


def test_collect_dashboard_computes_windows_and_breakdowns() -> None:
    async def scenario() -> None:
        await _reset_and_seed()
        engine = create_async_engine(_db_url(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                data = await collect_dashboard(session)
        finally:
            await engine.dispose()

        by_key = {metrics.period.key: metrics for metrics in data.periods}
        assert set(by_key) == {"24h", "7d", "30d"}

        day = by_key["24h"]
        assert day.requests == 3
        assert day.errors == 1
        assert day.error_rate == pytest.approx(1 / 3)
        assert day.cache_hits == 1
        assert day.cache_hit_rate == pytest.approx(1 / 3)
        assert (day.tokens_in, day.tokens_out) == (10, 5)
        assert day.cost_usd == pytest.approx(0.001)
        # Латентность — только model-a (100 мс) и model-b (900 мс); cache-hit
        # (2 мс) в перцентили не попадает.
        assert day.latency.count == 2
        assert day.latency.p50_ms == pytest.approx(500.0)
        assert day.latency.p95_ms == pytest.approx(860.0)
        assert day.latency.p99_ms == pytest.approx(892.0)
        assert day.latency.avg_ms == pytest.approx(500.0)

        model_rows = {row.key: row for row in day.by_model}
        assert set(model_rows) == {"model-a", "model-b", "semantic-cache"}
        assert model_rows["model-a"].tokens_total == 15
        assert model_rows["model-b"].errors == 1
        assert model_rows["semantic-cache"].cache_hits == 1
        level_rows = {row.key: row for row in day.by_level}
        assert level_rows["club"].requests == 2
        assert level_rows["gm"].requests == 1

        week = by_key["7d"]
        assert week.requests == 4
        assert week.errors == 1
        assert (week.tokens_in, week.tokens_out) == (30, 15)
        assert week.latency.p50_ms == pytest.approx(300.0)

        month = by_key["30d"]
        assert month.requests == 5
        assert month.errors == 2
        assert (month.tokens_in, month.tokens_out) == (35, 20)
        assert month.cost_usd == pytest.approx(0.006)

        # Top-5 из usage_daily: вне окна (40 дней) не учитывается.
        assert [row.subject for row in data.top_users["24h"]] == ["user:42"]
        assert [row.subject for row in data.top_users["7d"]] == [
            "user:42",
            "user:7",
        ]
        assert [row.subject for row in data.top_users["30d"]] == [
            "ip:1.2.3.4",
            "user:42",
            "user:7",
        ]
        assert data.top_users["30d"][0].tokens_total == 500

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


def test_collect_dashboard_empty_tables_is_all_zero() -> None:
    async def scenario() -> None:
        await _clear_tables()
        engine = create_async_engine(_db_url(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                data = await collect_dashboard(session)
        finally:
            await engine.dispose()

        for metrics in data.periods:
            assert metrics.requests == 0
            assert metrics.errors == 0
            assert metrics.error_rate == 0.0
            assert metrics.cache_hit_rate == 0.0
            assert metrics.latency.count == 0
            assert metrics.latency.p50_ms is None
            assert metrics.by_model == ()
            assert metrics.by_level == ()
        assert all(not rows for rows in data.top_users.values())

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_cleanup())


# --- Страница в админке ---------------------------------------------------


def test_dashboard_page_renders_for_admin_token(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    response = client.get(
        "/admin/llmops", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert response.status_code == 200, response.text
    text = response.text
    assert "LLMOps-дашборд" in text
    assert "Сводка" in text
    assert "model-a" in text
    assert "user:42" in text
    assert "Топ-5 пользователей" in text
    assert "p95" in text


def test_dashboard_page_allows_auditor_jwt(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, Any],
) -> None:
    response = client.get(
        "/admin/llmops", headers=_bearer(settings, users, "auditor")
    )
    assert response.status_code == 200, response.text


def test_dashboard_page_rejects_user_role(
    client: TestClient,
    settings: Settings,
    users: dict[str, str],
    seeded: dict[str, Any],
) -> None:
    response = client.get(
        "/admin/llmops", headers=_bearer(settings, users, "user")
    )
    assert response.status_code == 403
    assert response.json()["code"] == "admin_forbidden"


def test_dashboard_page_requires_credentials(
    client: TestClient, seeded: dict[str, Any]
) -> None:
    response = client.get("/admin/llmops")
    assert response.status_code == 401
    assert response.json()["code"] == "admin_unauthorized"


def test_dashboard_page_empty_state(client: TestClient) -> None:
    asyncio.run(_cleanup())
    response = client.get(
        "/admin/llmops", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert response.status_code == 200, response.text
    assert "Нет вызовов за окно." in response.text
    assert "Нет данных в <code>usage_daily</code>" in response.text
