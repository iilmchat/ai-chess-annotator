"""Rate limiting на Redis (T-009).

Простая фиксированная «минута» на ключ (IP + метод + путь):

```
current = INCR key
if current == 1: EXPIRE key <window>
allowed = current <= limit
```

Почему не `slowapi`: одна зависимость вместо целого фреймворка, поведение
прозрачно (два Redis-вызова), лимит и окно — из настроек.

Почему middleware, а не dependency: лимит должен считать **все** запросы к
защищённому пути, включая те, что упадут на валидации тела (иначе Flood
невалидным JSON бесплатен). Healthcheck не защищается — мониторинг не
должен упираться в лимит.

Если Redis недоступен, запросы **пропускаются** (fail-open): доступность
анализа важнее защиты от флуда, а факт деградации виден в healthcheck и
логах (KI-015).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import redis.asyncio as aioredis
import structlog
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

__all__ = ["RateLimitMiddleware", "RateLimitResult", "RedisRateLimiter"]

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Результат одного обращения к лимитеру."""

    allowed: bool
    limit: int
    remaining: int
    reset_after_s: int


class RedisRateLimiter:
    """Счётчик запросов в Redis с фиксированным окном."""

    def __init__(
        self, client: aioredis.Redis, *, limit: int, window_s: int
    ) -> None:
        if limit < 1:
            raise ValueError("limit должен быть ≥ 1")
        if window_s < 1:
            raise ValueError("window_s должен быть ≥ 1")
        self._client = client
        self._limit = limit
        self._window_s = window_s

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_s(self) -> int:
        return self._window_s

    async def hit(self, key: str) -> RateLimitResult:
        """Учесть запрос и сказать, можно ли его пропустить.

        Raises:
            Exception: любая ошибка Redis (middleware трактует её как
                fail-open).
        """
        current = int(await self._client.incr(key))
        if current == 1:
            # Окно ставится только на первом запросе; если процесс умрёт
            # между INCR и EXPIRE, ключ останется без TTL (см. KI-015).
            await self._client.expire(key, self._window_s)
        return RateLimitResult(
            allowed=current <= self._limit,
            limit=self._limit,
            remaining=max(0, self._limit - current),
            reset_after_s=self._window_s,
        )


class RateLimitMiddleware:
    """ASGI-middleware: лимит только для заданных `(method, path)`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected: Iterable[tuple[str, str]],
    ) -> None:
        self.app = app
        self._protected = frozenset(
            (method.upper(), path) for method, path in protected
        )

    async def __call__(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] != "http" or not self._protected:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        if (method, path) not in self._protected:
            await self.app(scope, receive, send)
            return

        limiter = _limiter_from_scope(scope)
        if limiter is None:
            await self.app(scope, receive, send)
            return

        key = f"ratelimit:{method}:{path}:{_client_ip(scope)}"
        try:
            result = await limiter.hit(key)
        except Exception as exc:
            logger.warning("rate_limit_unavailable", error=str(exc), path=path)
            await self.app(scope, receive, send)
            return

        if not result.allowed:
            logger.info("rate_limited", path=path, ip=_client_ip(scope))
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests",
                    "code": "rate_limited",
                },
                headers={
                    "Retry-After": str(result.reset_after_s),
                    "X-RateLimit-Limit": str(result.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _limiter_from_scope(scope: Scope) -> RedisRateLimiter | None:
    """Достать лимитер из `app.state` (создаётся в lifespan)."""
    app = scope.get("app")
    state = getattr(app, "state", None)
    limiter = getattr(state, "rate_limiter", None)
    return limiter if isinstance(limiter, RedisRateLimiter) else None


def _client_ip(scope: Scope) -> str:
    """IP клиента: `X-Forwarded-For` (первый) либо адрес сокета."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            forwarded = str(value.decode("latin-1")).split(",")[0].strip()
            if forwarded:
                return forwarded
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"
