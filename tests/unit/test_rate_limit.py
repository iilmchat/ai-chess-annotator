"""Тесты rate limiting (T-009).

Redis-клиент подменён фейком: проверяется арифметика окна, поведение
middleware (429, заголовки, fail-open) и то, что незащищённые пути не
считаются. Настоящий Redis в этих тестах не нужен.
"""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.api.ratelimit import RateLimitMiddleware, RedisRateLimiter


class FakeRedis:
    """Минимальный INCR/EXPIRE с флагом «сломаться»."""

    def __init__(self, *, fail: bool = False) -> None:
        self.counters: dict[str, int] = {}
        self.expires: dict[str, int] = {}
        self.incr_calls = 0
        self.fail = fail

    async def incr(self, key: str) -> int:
        self.incr_calls += 1
        if self.fail:
            raise ConnectionError("redis недоступен")
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


def _make_app(limiter: RedisRateLimiter | None) -> Starlette:
    async def analyze_endpoint(_request: object) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def health_endpoint(_request: object) -> JSONResponse:
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/v1/analyze", analyze_endpoint, methods=["POST"]),
            Route("/v1/health", health_endpoint, methods=["GET"]),
        ]
    )
    app.state.rate_limiter = limiter
    app.add_middleware(
        RateLimitMiddleware, protected=[("POST", "/v1/analyze")]
    )
    return app


# --- лимитер --------------------------------------------------------------


async def test_limiter_counts_and_blocks_after_limit() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, limit=2, window_s=60)  # type: ignore[arg-type]

    first = await limiter.hit("k")
    second = await limiter.hit("k")
    third = await limiter.hit("k")

    assert first.allowed is True
    assert first.remaining == 1
    assert second.allowed is True
    assert second.remaining == 0
    assert third.allowed is False
    assert third.reset_after_s == 60
    assert redis.expires["k"] == 60


async def test_limiter_sets_expire_only_on_first_hit() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, limit=5, window_s=30)  # type: ignore[arg-type]

    await limiter.hit("k")
    redis.expires.clear()
    await limiter.hit("k")

    assert redis.expires == {}


def test_limiter_validates_arguments() -> None:
    redis = FakeRedis()
    for kwargs in ({"limit": 0, "window_s": 60}, {"limit": 1, "window_s": 0}):
        try:
            RedisRateLimiter(redis, **kwargs)  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"ожидался ValueError для {kwargs}")


# --- middleware -----------------------------------------------------------


def test_middleware_blocks_after_limit_with_headers() -> None:
    limiter = RedisRateLimiter(FakeRedis(), limit=2, window_s=60)  # type: ignore[arg-type]
    client = TestClient(_make_app(limiter))

    assert client.post("/v1/analyze", json={"pgn": "x"}).status_code == 200
    assert client.post("/v1/analyze", json={"pgn": "x"}).status_code == 200
    blocked = client.post("/v1/analyze", json={"pgn": "x"})

    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited"
    assert blocked.headers["Retry-After"] == "60"
    assert blocked.headers["X-RateLimit-Limit"] == "2"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


def test_middleware_ignores_unprotected_paths() -> None:
    redis = FakeRedis()
    limiter = RedisRateLimiter(redis, limit=1, window_s=60)  # type: ignore[arg-type]
    client = TestClient(_make_app(limiter))

    for _ in range(5):
        assert client.get("/v1/health").status_code == 200

    assert redis.incr_calls == 0


def test_middleware_fails_open_when_redis_is_down() -> None:
    limiter = RedisRateLimiter(FakeRedis(fail=True), limit=1, window_s=60)  # type: ignore[arg-type]
    client = TestClient(_make_app(limiter))

    for _ in range(3):
        assert client.post("/v1/analyze", json={"pgn": "x"}).status_code == 200


def test_middleware_separates_clients_by_ip() -> None:
    limiter = RedisRateLimiter(FakeRedis(), limit=1, window_s=60)  # type: ignore[arg-type]
    client = TestClient(_make_app(limiter))

    first = client.post(
        "/v1/analyze", json={"pgn": "x"}, headers={"X-Forwarded-For": "10.0.0.1"}
    )
    second = client.post(
        "/v1/analyze", json={"pgn": "x"}, headers={"X-Forwarded-For": "10.0.0.2"}
    )
    third = client.post(
        "/v1/analyze", json={"pgn": "x"}, headers={"X-Forwarded-For": "10.0.0.1"}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_middleware_passes_through_without_limiter() -> None:
    client = TestClient(_make_app(None))

    assert client.post("/v1/analyze", json={"pgn": "x"}).status_code == 200
