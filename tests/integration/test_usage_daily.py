"""Durable FinOps-агрегаты `usage_daily` (T-031b) на живой БД.

Проверяем полный путь:

* `POST /v1/chat` (реальный `OpenAICompatibleClient` + fake HTTP) с
  включённым token budget → `record_token_usage` → фоновая задача upsert-а →
  строка `usage_daily` (`ip:testclient`) с токенами/стоимостью/запросами;
* DB-fallback `/v1/users/me/usage`, когда Redis-ключ удалён: `used` берётся
  из `usage_daily`;
* `scripts/aggregate_usage.py --json` на засеянных строках: суммы за месяц +
  retention удаляет старые записи.

Требует PostgreSQL и Stockfish (как остальные интеграционные тесты); без них
модуль скипается.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.models import Game, UsageDaily
from app.finops import utc_date, utc_day
from app.main import create_app
from scripts.aggregate_usage import main as aggregate_main

SHORT_PGN = (
    '[Event "FinOps"]\n[White "White"]\n[Black "Black"]\n[Result "1/2-1/2"]\n\n'
    "1. e4 e5 2. Nf3 Nc6 1/2-1/2"
)
SAFE_REPLY = "Партия началась спокойно."
FAKE_MODEL = "fake-llm"
TOKENS_IN = 11
TOKENS_OUT = 7
#: price_in=1.0, price_out=2.0 → (11 + 14) / 1e6.
EXPECTED_COST = 0.000025
#: IP-субъект тестового клиента Starlette/TestClient.
TEST_IP = "testclient"


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


def _stockfish_available() -> bool:
    return Path(get_settings().stockfish_path).is_file()


pytestmark = pytest.mark.skipif(
    not (_db_available() and _stockfish_available()),
    reason="PostgreSQL/Stockfish недоступны",
)


def _factory() -> async_sessionmaker[Any]:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    return async_sessionmaker(engine, expire_on_commit=False)


def _mock_llm() -> tuple[Any, httpx.AsyncClient]:
    from app.llm import OpenAICompatibleClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": FAKE_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": SAFE_REPLY},
                    }
                ],
                "usage": {
                    "prompt_tokens": TOKENS_IN,
                    "completion_tokens": TOKENS_OUT,
                },
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = OpenAICompatibleClient(
        base_url="http://llm.test/v1",
        model=FAKE_MODEL,
        provider_name="fake",
        client=http_client,
        price_in_usd_per_1m=1.0,
        price_out_usd_per_1m=2.0,
    )
    return llm, http_client


@pytest.fixture()
def settings() -> Any:
    return get_settings().model_copy(
        update={
            "rate_limit_enabled": False,
            "token_budget_enabled": True,
            "daily_token_limit_free": 10_000_000,
            "daily_token_limit_pro": 10_000_000,
            "llm_warmup_enabled": False,
            "semantic_cache_enabled": False,
            "prompt_pubsub_enabled": False,
            "otel_enabled": False,
        }
    )


@pytest.fixture()
def client(settings: Any) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _analyze(client: TestClient) -> int:
    response = client.post(
        "/v1/analyze", json={"pgn": SHORT_PGN, "level": "club"}
    )
    assert response.status_code == 200, response.text
    return int(response.json()["analysis_id"])


def _chat(client: TestClient) -> None:
    analysis_id = _analyze(client)
    response = client.post(
        "/v1/chat",
        json={
            "analysis_id": analysis_id,
            "level": "club",
            "message": "Кто играл белыми?",
        },
    )
    assert response.status_code == 200, response.text


def _wait_usage_row(
    *, subject_type: str, subject_key: str, timeout_s: float = 5.0
) -> UsageDaily | None:
    """Дождаться фонового durable-upsert-а (recorder пишет в фоне)."""
    async def read() -> UsageDaily | None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return await session.scalar(
                    select(UsageDaily).where(
                        UsageDaily.subject_type == subject_type,
                        UsageDaily.subject_key == subject_key,
                        UsageDaily.day == utc_date(),
                    )
                )
        finally:
            await engine.dispose()

    deadline = time.monotonic() + timeout_s
    while True:
        row = asyncio.run(read())
        if row is not None or time.monotonic() >= deadline:
            return row
        time.sleep(0.05)


async def _delete_usage_rows(
    *, subject_type: str, subject_key: str
) -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                delete(UsageDaily).where(
                    UsageDaily.subject_type == subject_type,
                    UsageDaily.subject_key == subject_key,
                )
            )
    finally:
        await engine.dispose()


async def _delete_game(game_id: int) -> None:
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                Game.__table__.delete().where(Game.id == game_id)
            )
    finally:
        await engine.dispose()


def _delete_redis_key(key: str) -> None:
    async def run() -> None:
        client = aioredis.from_url(
            get_settings().redis_url, decode_responses=True
        )
        try:
            await client.delete(key)
        finally:
            await client.aclose()

    asyncio.run(run())


def test_chat_request_writes_usage_daily(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    llm, http_client = _mock_llm()
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    try:
        _chat(client)
        row = _wait_usage_row(
            subject_type="ip", subject_key=TEST_IP
        )
        assert row is not None, "usage_daily не наполнился после LLM-вызова"
        assert row.day == utc_date()
        assert row.tokens_in == TOKENS_IN
        assert row.tokens_out == TOKENS_OUT
        assert float(row.cost_usd) == pytest.approx(EXPECTED_COST)
        assert row.requests == 1
    finally:
        asyncio.run(_delete_usage_rows(subject_type="ip", subject_key=TEST_IP))
        asyncio.run(http_client.aclose())


def test_usage_endpoint_falls_back_to_database(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Redis-ключ удалён (рестарт/сброс) — `/usage` отдаёт durable-данные."""
    llm, http_client = _mock_llm()
    monkeypatch.setattr(
        "app.chat.service.get_llm_client", lambda level, cfg: llm
    )
    try:
        _chat(client)
        assert _wait_usage_row(subject_type="ip", subject_key=TEST_IP)

        # Имитируем потерю Redis-счётчика: durable-строка остаётся.
        _delete_redis_key(f"usage:{utc_day()}:ip:{TEST_IP}")

        response = client.get("/v1/users/me/usage")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["used_tokens"] == TOKENS_IN + TOKENS_OUT
        assert body["remaining_tokens"] > 0
    finally:
        asyncio.run(_delete_usage_rows(subject_type="ip", subject_key=TEST_IP))
        asyncio.run(http_client.aclose())


def test_upsert_accumulates_requests_and_tokens() -> None:
    """`ON CONFLICT DO UPDATE` прибавляет дельты, а не перезаписывает."""
    day = date(2026, 1, 15)
    subject_key = "aggregate-test"

    async def seed_and_check() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            from app.finops import _upsert_usage_daily

            await _upsert_usage_daily(
                subject=f"user:{subject_key}",
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.1,
                day=day,
            )
            await _upsert_usage_daily(
                subject=f"user:{subject_key}",
                tokens_in=3,
                tokens_out=2,
                cost_usd=0.2,
                day=day,
            )
            async with factory() as session:
                row = await session.scalar(
                    select(UsageDaily).where(
                        UsageDaily.subject_type == "user",
                        UsageDaily.subject_key == subject_key,
                        UsageDaily.day == day,
                    )
                )
            assert row is not None
            assert (row.tokens_in, row.tokens_out) == (13, 7)
            assert float(row.cost_usd) == pytest.approx(0.3)
            assert row.requests == 2
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(UsageDaily).where(
                        UsageDaily.subject_key == subject_key
                    )
                )
            await engine.dispose()

    asyncio.run(seed_and_check())


def test_aggregate_usage_script_json_and_retention(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Скрипт печатает суммы месяца и удаляет старые строки."""
    month = "2026-10"
    day_in_month = date(2026, 10, 5)
    old_day = date(2020, 1, 1)

    async def seed() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                session.add_all(
                    [
                        UsageDaily(
                            subject_type="user",
                            subject_key="agg-1",
                            day=day_in_month,
                            tokens_in=100,
                            tokens_out=50,
                            cost_usd=0.5,
                            requests=2,
                        ),
                        UsageDaily(
                            subject_type="ip",
                            subject_key="agg-old",
                            day=old_day,
                            tokens_in=1,
                            tokens_out=1,
                            cost_usd=0.01,
                            requests=1,
                        ),
                    ]
                )
                await session.commit()
        finally:
            await engine.dispose()

    async def cleanup() -> None:
        engine = create_async_engine(
            get_settings().database_url, poolclass=NullPool
        )
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    delete(UsageDaily).where(
                        UsageDaily.subject_key.in_(("agg-1", "agg-old"))
                    )
                )
        finally:
            await engine.dispose()

    asyncio.run(seed())
    try:
        exit_code = aggregate_main(
            [
                "--month",
                month,
                "--json",
                "--retention-days",
                "0",
            ]
        )
        assert exit_code == 0
        report = json.loads(capsys.readouterr().out)
        assert report["month"] == month
        assert report["totals"]["tokens_in"] >= 100
        assert report["totals"]["tokens_out"] >= 50
        assert report["totals"]["requests"] >= 2
        assert report["retention"]["deleted"] >= 1
    finally:
        asyncio.run(cleanup())


def test_usage_daily_table_shape() -> None:
    """Санити-чек: уникальность и индексы usage_daily в метаданных."""
    from app.db.base import Base

    table = Base.metadata.tables["usage_daily"]
    assert table.c.day.nullable is False
    names = {constraint.name for constraint in table.constraints if constraint.name}
    assert "uq_usage_daily_subject_day" in names
    indexes = {index.name for index in table.indexes}
    assert "ix_usage_daily_day_subject" in indexes
    assert "ix_usage_daily_subject_day" in indexes
