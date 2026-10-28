"""Дневные бюджеты токенов (T-031).

Redis подменён фейком: проверяем INCRBY/TTL, 429 с заголовками,
разделение free/pro и fail-open при недоступном Redis. Реальный Redis и
LLM не нужны.
"""

from __future__ import annotations

import time

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.finops import (
    TokenBudgetMiddleware,
    TokenUsageTracker,
    record_token_usage,
)
from app.llm.openai_client import OpenAICompatibleClient


class FakeRedis:
    """Минимальные GET/INCRBY/EXPIRE с флагом «сломаться»."""

    def __init__(self, *, fail: bool = False) -> None:
        self.values: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.fail = fail

    async def get(self, key: str) -> int | None:
        if self.fail:
            raise ConnectionError("redis недоступен")
        return self.values.get(key)

    async def incrby(self, key: str, amount: int) -> int:
        if self.fail:
            raise ConnectionError("redis недоступен")
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


def _tracker(
    redis: FakeRedis, *, free: int = 100, pro: int = 500
) -> TokenUsageTracker:
    return TokenUsageTracker(
        redis,  # type: ignore[arg-type]
        free_limit=free,
        pro_limit=pro,
    )


def _make_app(
    tracker: TokenUsageTracker | None,
    *,
    pro_ips: set[str] | None = None,
    trust_forwarded_for: bool = False,
) -> Starlette:
    async def analyze_endpoint(_request: Request) -> JSONResponse:
        # Имитация LLM-вызова: клиент фиксирует фактический расход.
        await record_token_usage(60, 0)
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/v1/analyze", analyze_endpoint, methods=["POST"]),
        ]
    )
    app.state.token_usage_tracker = tracker
    app.add_middleware(
        TokenBudgetMiddleware,
        protected=[("POST", "/v1/analyze")],
        pro_ips=pro_ips or (),
        trust_forwarded_for=trust_forwarded_for,
    )
    return app


# --- трекер ---------------------------------------------------------------


async def test_tracker_adds_and_checks_daily_limit() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis)

    assert await tracker.used("ip:1.2.3.4") == 0
    assert await tracker.add("ip:1.2.3.4", 60) == 60
    assert await tracker.add("ip:1.2.3.4", 40) == 100

    status = await tracker.check("ip:1.2.3.4", "free")

    assert status.allowed is False
    assert status.used == 100
    assert status.remaining == 0
    assert status.limit == 100
    assert status.reset_at > int(time.time())


async def test_tracker_ignores_nonpositive_tokens() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis)

    assert await tracker.add("ip:1", 0) == 0
    assert redis.values == {}


async def test_tracker_sets_ttl_on_first_write() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis)

    await tracker.add("ip:1", 10)
    key = next(iter(redis.values))

    assert redis.expires[key] > 0


# --- middleware -----------------------------------------------------------


def test_third_request_is_blocked_by_token_budget() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis, free=100)
    client = TestClient(_make_app(tracker))

    first = client.post("/v1/analyze")
    second = client.post("/v1/analyze")
    third = client.post("/v1/analyze")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["code"] == "token_budget_exceeded"
    assert third.headers["X-RateLimit-Limit"] == "100"
    assert third.headers["X-RateLimit-Remaining"] == "0"
    assert third.headers["X-Token-Budget-Period"] == "day"
    assert int(third.headers["X-RateLimit-Reset"]) > int(time.time())

    key = next(iter(redis.values))
    assert redis.values[key] == 120


def test_pro_tier_is_granted_only_by_ip_whitelist() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=1000)
    client = TestClient(
        _make_app(
            tracker,
            pro_ips={"10.0.0.1"},
            trust_forwarded_for=True,
        )
    )

    headers = {"X-Forwarded-For": "10.0.0.1"}
    for _ in range(3):
        assert client.post("/v1/analyze", headers=headers).status_code == 200

    key = next(iter(redis.values))
    assert key.endswith("ip:10.0.0.1")
    assert redis.values[key] == 180


def test_user_tier_header_is_ignored() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=1000)
    client = TestClient(_make_app(tracker))

    spoofed = {"X-User-Tier": "pro", "X-User-Id": "alice"}
    assert client.post("/v1/analyze", headers=spoofed).status_code == 200
    blocked = client.post("/v1/analyze", headers=spoofed)

    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "10"
    key = next(iter(redis.values))
    assert key.endswith("ip:testclient")


def test_middleware_fails_open_when_redis_is_down() -> None:
    tracker = _tracker(FakeRedis(fail=True), free=1)
    client = TestClient(_make_app(tracker))

    for _ in range(3):
        assert client.post("/v1/analyze").status_code == 200


def test_middleware_passes_through_without_tracker() -> None:
    client = TestClient(_make_app(None))

    assert client.post("/v1/analyze").status_code == 200


def _llm_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "fake-model",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 30, "completion_tokens": 30},
        },
    )


def test_llm_client_records_tokens_inside_request() -> None:
    """Клиент сам пишет токены в счётчик, а не только middleware-тест."""
    redis = FakeRedis()
    tracker = _tracker(redis, free=100)

    async def endpoint(_request: Request) -> JSONResponse:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(_llm_response)
        ) as http_client:
            llm = OpenAICompatibleClient(
                base_url="http://llm.test/v1",
                model="fake-model",
                client=http_client,
            )
            await llm.complete("prompt")
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/v1/analyze", endpoint, methods=["POST"])])
    app.state.token_usage_tracker = tracker
    app.add_middleware(
        TokenBudgetMiddleware, protected=[("POST", "/v1/analyze")]
    )
    client = TestClient(app)

    assert client.post("/v1/analyze").status_code == 200
    assert client.post("/v1/analyze").status_code == 200
    third = client.post("/v1/analyze")

    assert third.status_code == 429
    key = next(iter(redis.values))
    assert redis.values[key] == 120


async def test_record_usage_is_noop_outside_request() -> None:
    # Не должно падать и не должно требовать Redis.
    await record_token_usage(10, 10)
