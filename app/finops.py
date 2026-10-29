"""FinOps: дневные бюджеты токенов и учёт расхода (T-031, KI-038).

Счётчик — Redis `usage:{day}:{subject}` с `INCRBY`; TTL — до ближайшей
UTC-полуночи плюс минута. Проверка выполняется **до** вызова LLM, поэтому
превышение лимита возвращает 429 без расхода токенов.

Субъект счётчика — проверенный JWT-пользователь (`user:{id}`), если в
запросе есть валидный access-токен; иначе — IP клиента (`ip:{ip}`), как
было до JWT (KI-031). Токен проверяется только подписью/сроком
(`decode_token`), без запроса в БД: middleware работает на каждом запросе,
а FinOps не должен зависеть от доступности PostgreSQL. Битый, просроченный
или refresh-токен не даёт 401 — запрос считается анонимным (KI-038).

Почему Redis, а не БД: проверка должна быть дешёвой и атомарной на каждом
запросе. Durable-агрегаты (`usage_daily`) и точный reserve/reconcile —
следующий шаг T-031; здесь — горячий счётчик и жёсткий дневной потолок.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
import structlog
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.tokens import decode_token
from app.config import Settings
from app.errors import AuthError

__all__ = [
    "PRO_ROLES",
    "TokenBudgetMiddleware",
    "TokenBudgetStatus",
    "TokenUsageTracker",
    "compute_cost_usd",
    "next_utc_midnight",
    "prices_for_level",
    "record_token_usage",
    "request_subject",
    "request_tier",
    "seconds_until_reset",
    "utc_day",
]

logger = structlog.get_logger(__name__)

#: Роли из claim `role`, которым положен «профессиональный» дневной лимит.
#: Строки, а не `AdminRole`, чтобы FinOps-слой не зависел от моделей БД.
PRO_ROLES = frozenset({"admin", "auditor"})

#: Текущий трекер и субъект запроса; выставляются middleware и читаются
#: LLM-клиентом после ответа (без протаскивания Redis через все слои).
_current_tracker: contextvars.ContextVar[TokenUsageTracker | None] = (
    contextvars.ContextVar("token_tracker", default=None)
)
_current_subject: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "token_subject", default=None
)


def utc_day(now: datetime | None = None) -> str:
    """Дата в UTC в формате ``YYYY-MM-DD`` (ключ счётчика)."""
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def next_utc_midnight(now: datetime | None = None) -> datetime:
    """Ближайшая полночь UTC (когда сбрасывается дневной бюджет)."""
    moment = now or datetime.now(UTC)
    tomorrow = moment.date() + timedelta(days=1)
    return datetime(
        tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC
    )


def seconds_until_reset(now: datetime | None = None) -> int:
    """Сколько секунд до сброса дневного бюджета."""
    moment = now or datetime.now(UTC)
    return max(1, int((next_utc_midnight(moment) - moment).total_seconds()))


def compute_cost_usd(
    tokens_in: int,
    tokens_out: int,
    *,
    price_in_per_1m: float,
    price_out_per_1m: float,
) -> float:
    """Стоимость вызова по цене за 1M токенов, округлённая до 6 знаков."""
    return round(
        (
            tokens_in * price_in_per_1m
            + tokens_out * price_out_per_1m
        )
        / 1_000_000,
        6,
    )


def prices_for_level(settings: Settings, level: str) -> tuple[float, float]:
    """Цены (in, out) для уровня: локальные — beginner/club, облачные — gm."""
    if level in {"beginner", "club"}:
        return (
            settings.llm_local_price_in_usd_per_1m,
            settings.llm_local_price_out_usd_per_1m,
        )
    return (
        settings.llm_price_in_usd_per_1m,
        settings.llm_price_out_usd_per_1m,
    )


@dataclass(frozen=True, slots=True)
class TokenBudgetStatus:
    """Результат проверки дневного бюджета."""

    allowed: bool
    limit: int
    used: int
    remaining: int
    reset_at: int
    retry_after_s: int


class TokenUsageTracker:
    """Счётчики токенов в Redis с дневным окном."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        free_limit: int,
        pro_limit: int,
    ) -> None:
        if free_limit < 0 or pro_limit < 0:
            raise ValueError("дневные лимиты токенов должны быть ≥ 0")
        self._client = client
        self._limits = {"free": free_limit, "pro": pro_limit}

    @staticmethod
    def _key(subject: str, day: str) -> str:
        return f"usage:{day}:{subject}"

    async def used(self, subject: str, *, day: str | None = None) -> int:
        """Сколько токенов уже израсходовано субъектом за день."""
        raw = await self._client.get(self._key(subject, day or utc_day()))
        return int(raw or 0)

    async def add(
        self,
        subject: str,
        tokens: int,
        *,
        day: str | None = None,
    ) -> int:
        """Прибавить токены и вернуть новое значение счётчика."""
        if tokens <= 0:
            return await self.used(subject, day=day)
        key = self._key(subject, day or utc_day())
        total = int(await self._client.incrby(key, tokens))
        if total == tokens:
            await self._client.expire(key, seconds_until_reset() + 60)
        return total

    async def check(
        self,
        subject: str,
        tier: str = "free",
        *,
        day: str | None = None,
    ) -> TokenBudgetStatus:
        """Проверить, не исчерпан ли дневной лимит субъекта."""
        limit = self._limits.get(tier, self._limits["free"])
        used = await self.used(subject, day=day)
        reset_at = int(next_utc_midnight().timestamp())
        return TokenBudgetStatus(
            allowed=used < limit,
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
            reset_at=reset_at,
            retry_after_s=seconds_until_reset(),
        )


async def record_token_usage(tokens_in: int, tokens_out: int) -> None:
    """Записать расход токенов в текущий дневной счётчик.

    No-op вне HTTP-запроса (скрипты/eval) и при недоступном Redis:
    observability/FinOps не должна ломать генерацию.
    """
    tracker = _current_tracker.get()
    subject = _current_subject.get()
    if tracker is None or subject is None:
        return
    try:
        await tracker.add(subject, tokens_in + tokens_out)
    except Exception as exc:  # fail-open, как у rate limiting
        logger.warning("token_usage_record_failed", error=str(exc))


class TokenBudgetMiddleware:
    """ASGI-middleware: 429, если дневной бюджет токенов исчерпан.

    Субъект и tier определяются по проверенному access-токену
    (`user:{id}` + роль), иначе — по IP и белому списку `PRO_IPS` (KI-038).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        settings: Settings,
        protected: Iterable[tuple[str, str]],
    ) -> None:
        self.app = app
        self._settings = settings
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

        tracker = _tracker_from_scope(scope)
        if tracker is None:
            await self.app(scope, receive, send)
            return

        subject, tier = _identity_from_scope(scope, self._settings)
        try:
            status = await tracker.check(subject, tier)
        except Exception as exc:
            logger.warning(
                "token_budget_unavailable", error=str(exc), path=path
            )
            await self.app(scope, receive, send)
            return

        if not status.allowed:
            logger.info(
                "token_budget_exceeded",
                subject=subject,
                tier=tier,
                used=status.used,
                limit=status.limit,
            )
            response = JSONResponse(
                status_code=429,
                content={
                    "detail": "Daily token budget exceeded",
                    "code": "token_budget_exceeded",
                },
                headers={
                    "Retry-After": str(status.retry_after_s),
                    "X-RateLimit-Reset": str(status.reset_at),
                    "X-RateLimit-Limit": str(status.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-Token-Budget-Period": "day",
                },
            )
            await response(scope, receive, send)
            return

        tracker_token = _current_tracker.set(tracker)
        subject_token = _current_subject.set(subject)
        try:
            await self.app(scope, receive, send)
        finally:
            _current_subject.reset(subject_token)
            _current_tracker.reset(tracker_token)


def _tracker_from_scope(scope: Scope) -> TokenUsageTracker | None:
    app = scope.get("app")
    state = getattr(app, "state", None)
    tracker = getattr(state, "token_usage_tracker", None)
    return tracker if isinstance(tracker, TokenUsageTracker) else None


def request_subject(request: Request, settings: Settings) -> str:
    """Публичный subject запроса — тот же, что у middleware (JWT-aware)."""
    return _identity_from_scope(request.scope, settings)[0]


def request_tier(request: Request, settings: Settings) -> str:
    """Публичный tier запроса — тот же, что у middleware (JWT-aware)."""
    return _identity_from_scope(request.scope, settings)[1]


def _identity_from_scope(scope: Scope, settings: Settings) -> tuple[str, str]:
    """``(subject, tier)`` запроса: JWT-пользователь, иначе IP и `PRO_IPS`.

    Токен проверяется только подписью и сроком, без запроса в БД: слой
    FinOps не зависит от PostgreSQL. Отсутствующий/битый/просроченный токен
    — не ошибка, а анонимный запрос (KI-038).
    """
    identity = _jwt_identity(scope, settings)
    if identity is not None:
        return identity
    client_ip = _client_ip(scope, settings.trust_forwarded_for)
    # KI-031: pro определяется доверенным IP, а не заголовком клиента.
    tier = "pro" if client_ip in settings.pro_ips_set else "free"
    return f"ip:{client_ip}", tier


def _jwt_identity(scope: Scope, settings: Settings) -> tuple[str, str] | None:
    """``("user:{id}", tier по роли)`` из валидного access-токена или ``None``."""
    token = _bearer_token(scope)
    if token is None:
        return None
    try:
        payload = decode_token(token, settings, expected_type="access")
    except AuthError:
        return None
    tier = "pro" if payload.role in PRO_ROLES else "free"
    return f"user:{payload.user_id}", tier


def _bearer_token(scope: Scope) -> str | None:
    """Значение ``Authorization: Bearer <token>`` из ASGI-scope."""
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name.lower() != b"authorization":
            continue
        scheme, _, token = value.decode("latin-1").strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
        return None
    return None


def _client_ip(scope: Scope, trust_forwarded_for: bool) -> str:
    """IP клиента: XFF только при доверенном прокси, иначе адрес сокета."""
    if trust_forwarded_for:
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                forwarded = (
                    str(value.decode("latin-1")).split(",")[0].strip()
                )
                if forwarded:
                    return forwarded
    client = scope.get("client")
    if client:
        return str(client[0])
    return "unknown"
