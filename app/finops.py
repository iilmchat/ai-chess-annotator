"""FinOps: дневные бюджеты токенов и учёт расхода (T-031, T-031b, KI-038).

Счётчик — Redis `usage:{day}:{subject}` с `INCRBY`; TTL — до ближайшей
UTC-полуночи плюс минута. Проверка выполняется **до** вызова LLM, поэтому
превышение лимита возвращает 429 без расхода токенов.

Субъект счётчика — проверенный JWT-пользователь (`user:{id}`), если в
запросе есть валидный access-токен; иначе — IP клиента (`ip:{ip}`), как
было до JWT (KI-031). Токен проверяется только подписью/сроком
(`decode_token`), без запроса в БД: middleware работает на каждом запросе,
а FinOps не должен зависеть от доступности PostgreSQL. Битый, просроченный
или refresh-токен не даёт 401 — запрос считается анонимным (KI-038).

Durable-агрегат — `usage_daily` (T-031b): `record_token_usage` после
Redis-инкремента best-effort upsert-ит дельту в PostgreSQL
(`ON CONFLICT DO UPDATE`). Redis остаётся горячим счётчиком для 429;
`usage_daily` — durable-история для отчётов и fallback в
`/v1/users/me/usage`, когда Redis недоступен или пуст.

Почему Redis остаётся горячим слоем: проверка должна быть дешёвой и
атомарной на каждом запросе, а PostgreSQL — durable-источником истины для
отчётов. Счётчик может отставать от БД на время фоновой записи — это
осознанный компромисс; 429 считается по Redis, а `usage_daily` даёт
fallback, если Redis пуст/недоступен.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import redis.asyncio as aioredis
import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.auth.tokens import decode_token
from app.config import Settings
from app.db.models import UsageDaily
from app.db.session import SessionLocal
from app.errors import AuthError

__all__ = [
    "PRO_ROLES",
    "TokenBudgetMiddleware",
    "TokenBudgetStatus",
    "TokenUsageTracker",
    "compute_cost_usd",
    "next_utc_midnight",
    "parse_subject",
    "prices_for_level",
    "record_token_usage",
    "request_subject",
    "request_tier",
    "seconds_until_reset",
    "usage_daily_tokens",
    "utc_date",
    "utc_day",
    "wait_for_pending_usage_upserts",
]

logger = structlog.get_logger(__name__)

#: Роли из claim `role`, которым положен «профессиональный» дневной лимит.
#: Строки, а не `AdminRole`, чтобы FinOps-слой не зависел от моделей БД.
PRO_ROLES = frozenset({"admin", "auditor"})

#: Допустимые типы субъекта в `usage_daily.subject_type`.
_SUBJECT_TYPES = frozenset({"user", "ip"})

#: Текущий трекер и субъект запроса; выставляются middleware и читаются
#: LLM-клиентом после ответа (без протаскивания Redis через все слои).
_current_tracker: contextvars.ContextVar[TokenUsageTracker | None] = (
    contextvars.ContextVar("token_tracker", default=None)
)
_current_subject: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "token_subject", default=None
)

#: Ссылки на фоновые задачи durable-upsert: без них GC может отменить запись.
_pending_usage_upserts: set[asyncio.Task[None]] = set()


def utc_day(now: datetime | None = None) -> str:
    """Дата в UTC в формате ``YYYY-MM-DD`` (ключ счётчика)."""
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def utc_date(now: datetime | None = None) -> date:
    """UTC-дата дня (колонка `usage_daily.day`)."""
    return (now or datetime.now(UTC)).date()


def parse_subject(subject: str) -> tuple[str, str]:
    """Разложить subject счётчика на ``(subject_type, subject_key)``.

    ``user:42`` → ``("user", "42")``, ``ip:1.2.3.4`` → ``("ip", "1.2.3.4")``.

    Raises:
        ValueError: неизвестный префикс или пустой ключ.
    """
    subject_type, separator, subject_key = subject.partition(":")
    if not separator or subject_type not in _SUBJECT_TYPES or not subject_key:
        raise ValueError(f"неизвестный субъект счётчика: {subject!r}")
    return subject_type, subject_key


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


async def record_token_usage(
    tokens_in: int, tokens_out: int, cost_usd: float = 0.0
) -> None:
    """Записать расход токенов в Redis-счётчик и durable `usage_daily`.

    Redis — горячий путь (429), `usage_daily` — durable-агрегат для отчётов
    и fallback в `/v1/users/me/usage`. Обе записи best-effort и не ломают
    генерацию: ошибка Redis логируется, durable-upsert уходит в фон
    отдельной задачей (см. `wait_for_pending_usage_upserts` для тестов).

    No-op вне HTTP-запроса (скрипты/eval): субъект счётчика выставляет
    `TokenBudgetMiddleware` в contextvar.

    Args:
        tokens_in: входные токены вызова.
        tokens_out: выходные токены вызова.
        cost_usd: стоимость вызова по прайсу на момент вызова.
    """
    subject = _current_subject.get()
    if subject is None:
        return
    tracker = _current_tracker.get()
    if tracker is not None:
        try:
            await tracker.add(subject, tokens_in + tokens_out)
        except Exception as exc:  # fail-open, как у rate limiting
            logger.warning("token_usage_record_failed", error=str(exc))
    # Durable-агрегат не зависит от Redis: пишем даже если инкремент упал.
    _schedule_usage_upsert(
        subject=subject,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
    )


def _schedule_usage_upsert(
    *,
    subject: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    requests: int = 1,
    day: date | None = None,
) -> None:
    """Поставить durable-upsert дельты в фон (fire-and-forget, best-effort)."""
    try:
        task = asyncio.create_task(
            _upsert_usage_daily(
                subject=subject,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                requests=requests,
                day=day,
            ),
            name="usage-daily-upsert",
        )
        _pending_usage_upserts.add(task)
        task.add_done_callback(_pending_usage_upserts.discard)
    except Exception as exc:  # например, event loop уже закрыт
        logger.warning(
            "usage_daily_schedule_failed", error=str(exc), subject=subject
        )


async def _upsert_usage_daily(
    *,
    subject: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    requests: int = 1,
    day: date | None = None,
) -> None:
    """Прибавить дельту к `usage_daily` одним `INSERT ... ON CONFLICT`.

    `ON CONFLICT (subject_type, subject_key, day) DO UPDATE` атомарно
    прибавляет токены/стоимость и инкрементирует `requests`; отдельная
    сессия и фоновая задача — чтобы не блокировать ответ LLM.
    """
    try:
        subject_type, subject_key = parse_subject(subject)
        table = UsageDaily.__table__
        statement = pg_insert(UsageDaily).values(
            subject_type=subject_type,
            subject_key=subject_key,
            day=day or utc_date(),
            tokens_in=max(0, int(tokens_in)),
            tokens_out=max(0, int(tokens_out)),
            cost_usd=Decimal(str(max(0.0, float(cost_usd)))),
            requests=max(0, int(requests)),
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_usage_daily_subject_day",
            set_={
                "tokens_in": table.c.tokens_in + statement.excluded.tokens_in,
                "tokens_out": (
                    table.c.tokens_out + statement.excluded.tokens_out
                ),
                "cost_usd": table.c.cost_usd + statement.excluded.cost_usd,
                "requests": table.c.requests + statement.excluded.requests,
                "updated_at": func.now(),
            },
        )
        async with SessionLocal() as session:
            await session.execute(statement)
            await session.commit()
    except Exception as exc:  # LLMOps/FinOps не должны ломать запрос
        logger.warning(
            "usage_daily_upsert_failed", error=str(exc), subject=subject
        )


async def wait_for_pending_usage_upserts(timeout_s: float = 2.0) -> None:
    """Дождаться фоновых durable-upsert-ов (тесты/корректное завершение).

    Без этого фоновые задачи могут быть отменены при закрытии event loop
    (TestClient), и тест не увидит строку в `usage_daily`.
    """
    tasks = [task for task in list(_pending_usage_upserts) if not task.done()]
    if tasks:
        await asyncio.wait(tasks, timeout=timeout_s)


async def usage_daily_tokens(
    subject: str, *, day: date | None = None
) -> int | None:
    """Токены субъекта за день из `usage_daily` (fallback для usage endpoint).

    Returns:
        ``tokens_in + tokens_out`` или ``None``, если строки нет (или БД
        недоступна): вызывающий отличает «данных нет» от нулевого расхода.

    Ошибки БД логируются и не пробрасываются: FinOps best-effort.
    """
    try:
        subject_type, subject_key = parse_subject(subject)
    except ValueError as exc:
        logger.warning("usage_daily_subject_invalid", error=str(exc))
        return None
    try:
        async with SessionLocal() as session:
            total = await session.scalar(
                select(UsageDaily.tokens_in + UsageDaily.tokens_out).where(
                    UsageDaily.subject_type == subject_type,
                    UsageDaily.subject_key == subject_key,
                    UsageDaily.day == (day or utc_date()),
                )
            )
    except Exception as exc:  # БД недоступна — endpoint отдаст Redis-путь
        logger.warning(
            "usage_daily_read_failed", error=str(exc), subject=subject
        )
        return None
    return int(total) if total is not None else None


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
