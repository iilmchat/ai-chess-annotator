"""Дневные бюджеты токенов (T-031, KI-038).

Redis подменён фейком: проверяем INCRBY/TTL, 429 с заголовками,
разделение free/pro и fail-open при недоступном Redis. Реальный Redis и
LLM не нужны.

T-083: субъект счётчика — JWT-пользователь (`user:{id}`), тир читается из
`users.tier`; аноним — IP и всегда `free` (`PRO_IPS` больше не действует).
БД в unit-тестах подменена функцией `tier_lookup`; токены подписываются
реальным хелпером auth, `decode_token` проверяет подпись/срок/тип.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.auth.tokens import create_access_token, create_refresh_token
from app.config import Settings
from app.db.models import AdminRole, Level, User
from app.finops import (
    TierLookup,
    TokenBudgetMiddleware,
    TokenUsageTracker,
    record_token_usage,
    request_subject,
    request_tier,
)
from app.llm.openai_client import OpenAICompatibleClient

JWT_SECRET = "unit-token-budget-secret-0123456789"


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


def _settings(**overrides: object) -> Settings:
    """Настройки теста: свой JWT-секрет, пустой белый список IP."""
    values: dict[str, object] = {
        "app_env": "test",
        "jwt_secret": JWT_SECRET,
        "pro_ips": "",
        "trust_forwarded_for": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


async def _free_tier(_user_id: int) -> str:
    """Заглушка БД: у пользователя тариф free."""
    return "free"


def _tier(value: str) -> TierLookup:
    """Заглушка lookup-а `users.tier` для unit-тестов без PostgreSQL."""

    async def lookup(_user_id: int) -> str:
        return value

    return lookup


def _user(user_id: int, role: AdminRole) -> User:
    """Несохранённый пользователь: нужны только id и role для claim-ов."""
    return User(
        id=user_id,
        email=f"unit-{user_id}@example.com",
        level=Level.club,
        role=role,
        is_active=True,
    )


def _access_token(role: AdminRole, *, user_id: int = 42) -> str:
    return create_access_token(_user(user_id, role), _settings())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _request(
    headers: dict[str, str] | None = None,
    *,
    client_ip: str = "1.2.3.4",
) -> Request:
    """Request с ASGI-scope: без сети и без запуска приложения."""
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/users/me/usage",
        "headers": raw_headers,
        "client": (client_ip, 1234),
    }
    return Request(scope)


def _make_app(
    tracker: TokenUsageTracker | None,
    *,
    settings: Settings | None = None,
    tier_lookup: TierLookup | None = None,
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
        settings=settings or _settings(),
        tier_lookup=tier_lookup or _free_tier,
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


# --- резолвер subject/tier (публичный API для usage) -----------------------


@pytest.mark.parametrize("tier", ["free", "pro"])
def test_request_tier_comes_from_users_tier(tier: str) -> None:
    """T-083: тир — из `users.tier`, роль admin/user на него не влияет."""
    settings = _settings()
    request = _request(_auth(_access_token(AdminRole.user)))

    assert request_subject(request, settings) == "user:42"
    assert (
        asyncio.run(
            request_tier(request, settings, tier_lookup=_tier(tier))
        )
        == tier
    )


def test_tier_lookup_receives_jwt_user_id() -> None:
    seen: list[int] = []

    async def lookup(user_id: int) -> str:
        seen.append(user_id)
        return "pro"

    settings = _settings()
    request = _request(_auth(_access_token(AdminRole.user, user_id=77)))

    assert asyncio.run(
        request_tier(request, settings, tier_lookup=lookup)
    ) == "pro"
    assert seen == [77]


def test_request_subject_falls_back_to_ip_without_token() -> None:
    request = _request()

    assert request_subject(request, _settings()) == "ip:1.2.3.4"
    assert (
        asyncio.run(
            request_tier(request, _settings(), tier_lookup=_free_tier)
        )
        == "free"
    )


def test_request_subject_falls_back_to_ip_for_broken_token() -> None:
    request = _request(_auth("not-a-valid-jwt"))

    assert request_subject(request, _settings()) == "ip:1.2.3.4"
    assert (
        asyncio.run(
            request_tier(request, _settings(), tier_lookup=_free_tier)
        )
        == "free"
    )


def test_refresh_token_does_not_authorize_a_subject() -> None:
    """FinOps принимает только access-токен; refresh — аноним (KI-038)."""
    settings = _settings()
    refresh = create_refresh_token(_user(42, AdminRole.admin), settings)
    request = _request(_auth(refresh))

    assert request_subject(request, settings) == "ip:1.2.3.4"
    assert (
        asyncio.run(
            request_tier(request, settings, tier_lookup=_free_tier)
        )
        == "free"
    )


def test_pro_ip_whitelist_is_ignored() -> None:
    """T-083: `PRO_IPS` больше не выдаёт Pro — аноним всегда free."""
    settings = _settings(pro_ips="1.2.3.4")
    request = _request()

    assert request_subject(request, settings) == "ip:1.2.3.4"
    assert (
        asyncio.run(
            request_tier(request, settings, tier_lookup=_free_tier)
        )
        == "free"
    )


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


def test_pro_tier_uses_pro_limit() -> None:
    """T-083: `users.tier='pro'` — субъект `user:{id}` и лимит pro."""
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=100)
    client = TestClient(_make_app(tracker, tier_lookup=_tier("pro")))
    headers = _auth(_access_token(AdminRole.user))

    assert client.post("/v1/analyze", headers=headers).status_code == 200
    # 60 < 100, поэтому pro-запрос проходит; free-лимит (10) дал бы 429.
    assert client.post("/v1/analyze", headers=headers).status_code == 200
    blocked = client.post("/v1/analyze", headers=headers)

    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "100"
    key = next(iter(redis.values))
    assert key.endswith("user:42")
    assert redis.values[key] == 120


def test_user_token_uses_free_limit() -> None:
    """Обычный пользователь — `user:{id}`, но лимит free."""
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=1000)
    client = TestClient(_make_app(tracker))
    headers = _auth(_access_token(AdminRole.user, user_id=7))

    assert client.post("/v1/analyze", headers=headers).status_code == 200
    blocked = client.post("/v1/analyze", headers=headers)

    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "10"
    key = next(iter(redis.values))
    assert key.endswith("user:7")


def test_broken_token_falls_back_to_ip_instead_of_401() -> None:
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=1000)
    client = TestClient(_make_app(tracker))
    headers = _auth("garbage-token")

    assert client.post("/v1/analyze", headers=headers).status_code == 200
    blocked = client.post("/v1/analyze", headers=headers)

    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "10"
    key = next(iter(redis.values))
    assert key.endswith("ip:testclient")


def test_pro_ip_whitelist_no_longer_grants_pro() -> None:
    """T-083: аноним с «pro» IP получает free-лимит и 429 на третьем запросе."""
    redis = FakeRedis()
    tracker = _tracker(redis, free=10, pro=1000)
    client = TestClient(
        _make_app(
            tracker,
            settings=_settings(
                pro_ips="10.0.0.1", trust_forwarded_for=True
            ),
        )
    )

    headers = {"X-Forwarded-For": "10.0.0.1"}
    first = client.post("/v1/analyze", headers=headers)
    # Эндпоинт-заглушка пишет 60 токенов: после первого запроса free-лимит
    # (10) уже исчерпан — pro по IP больше не спасает.
    blocked = client.post("/v1/analyze", headers=headers)

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Limit"] == "10"
    key = next(iter(redis.values))
    assert key.endswith("ip:10.0.0.1")
    assert redis.values[key] == 60


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
        TokenBudgetMiddleware,
        protected=[("POST", "/v1/analyze")],
        settings=_settings(),
        tier_lookup=_free_tier,
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
