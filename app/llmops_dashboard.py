"""LLMOps-дашборд в админ-панели (T-109, итерация 29).

Метрики качества/стоимости LLM уже лежат в БД: `llm_calls` (durable-история
вызовов) и `usage_daily` (FinOps-агрегат). Раньше их можно было смотреть
только через SQL-консоль или read-only view `LlmCallAdmin` (по одной строке).

Здесь собираются агрегаты за последние 24 часа, 7 и 30 дней:

* **Latency** — p50/p95/p99 и среднее по `latency_ms`; считаются только
  реальные вызовы провайдера (`cache_hit = false`), иначе попадания в
  semantic cache с околонулевой латентностью занижали бы перцентили.
* **Tokens & Cost** — суммы `tokens_in`/`tokens_out`/`cost_usd` с
  разбивкой по `model` и `level`.
* **Errors** — количество вызовов со статусом `error`/`timeout` и error
  rate (доля от всех вызовов окна).
* **Cache** — количество `cache_hit = true` и hit rate.
* **Top-5 users** — из `usage_daily` по сумме токенов за окно
  (гранулярность таблицы — день, поэтому окно «24 часа» включает текущие
  сутки UTC).

Модуль не зависит от starlette-admin, кроме самого `CustomView`: SQL-сбор
(`collect_dashboard`) тестируется отдельно, view только рендерит шаблон
`app/templates/llmops_dashboard.html` (Jinja2 + Bootstrap, без React).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from starlette.requests import Request
from starlette.responses import Response
from starlette_admin import CustomView, route

from app.db.models import LlmCall, UsageDaily
from app.db.session import SessionLocal

__all__ = [
    "PERIODS",
    "DashboardData",
    "LatencyStats",
    "LlmOpsDashboardView",
    "ModelUsage",
    "Period",
    "PeriodMetrics",
    "TopUserRow",
    "collect_dashboard",
]

#: Статусы `llm_calls`, которые считаются ошибкой.
ERROR_STATUSES: tuple[str, ...] = ("error", "timeout")

#: Сколько строк показывать в разбивке «по модели»/«по уровню».
GROUP_LIMIT = 15

#: Сколько пользователей показывать в виджете «Топ-5».
TOP_USERS_LIMIT = 5


@dataclass(frozen=True, slots=True)
class Period:
    """Окно агрегации дашборда."""

    key: str
    label: str
    description: str
    delta: timedelta


#: Окна, за которые считается сводка (T-109).
PERIODS: tuple[Period, ...] = (
    Period("24h", "24 часа", "последние 24 часа", timedelta(hours=24)),
    Period("7d", "7 дней", "последние 7 дней", timedelta(days=7)),
    Period("30d", "30 дней", "последние 30 дней", timedelta(days=30)),
)

@dataclass(frozen=True, slots=True)
class LatencyStats:
    """Перцентили латентности реальных вызовов LLM (без cache-hit)."""

    count: int = 0
    avg_ms: float | None = None
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Агрегат по модели (или уровню) за окно."""

    key: str
    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    cache_hits: int = 0

    @property
    def tokens_total(self) -> int:
        """Суммарные токены (in + out)."""
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True, slots=True)
class TopUserRow:
    """Строка виджета «Топ-5 пользователей по расходу токенов»."""

    subject_type: str
    subject_key: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    requests: int = 0

    @property
    def tokens_total(self) -> int:
        """Суммарные токены (in + out)."""
        return self.tokens_in + self.tokens_out

    @property
    def subject(self) -> str:
        """Человекочитаемый субъект: ``user:42`` / ``ip:…``."""
        return f"{self.subject_type}:{self.subject_key}"


@dataclass(frozen=True, slots=True)
class PeriodMetrics:
    """Сводка за одно окно: счётчики, латентность и разбивки."""

    period: Period
    since: datetime
    requests: int = 0
    errors: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency: LatencyStats = LatencyStats()
    by_model: tuple[ModelUsage, ...] = ()
    by_level: tuple[ModelUsage, ...] = ()

    @property
    def error_rate(self) -> float:
        """Доля ошибок/таймаутов от всех вызовов окна (0..1)."""
        return _rate(self.errors, self.requests)

    @property
    def cache_hit_rate(self) -> float:
        """Доля cache-hit от всех вызовов окна (0..1)."""
        return _rate(self.cache_hits, self.requests)

    @property
    def tokens_total(self) -> int:
        """Суммарные токены (in + out)."""
        return self.tokens_in + self.tokens_out


@dataclass(frozen=True, slots=True)
class DashboardData:
    """Всё, что нужно странице дашборда (готово к рендеру)."""

    generated_at: datetime
    periods: tuple[PeriodMetrics, ...]
    top_users: dict[str, tuple[TopUserRow, ...]]


def _rate(numerator: int, denominator: int) -> float:
    """Доля ``numerator / denominator``; 0.0 при пустом знаменателе."""
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _to_float(value: Any) -> float | None:
    """Decimal/int/None из БД → float или None."""
    if value is None:
        return None
    return float(value)


def _group_key(value: Any) -> str:
    """Ключ группировки: enum → значение, NULL → «—»."""
    if value is None:
        return "—"
    if isinstance(value, enum.Enum):
        return str(value.value)
    return str(value)


async def _collect_totals(
    session: AsyncSession, *, since: datetime
) -> tuple[int, int, int, int, int, float]:
    """Счётчики окна: (requests, errors, cache_hits, tokens_in, tokens_out, cost)."""
    row = (
        await session.execute(
            select(
                func.count().label("requests"),
                func.count()
                .filter(LlmCall.status.in_(ERROR_STATUSES))
                .label("errors"),
                func.count()
                .filter(LlmCall.cache_hit.is_(True))
                .label("cache_hits"),
                func.coalesce(func.sum(LlmCall.tokens_in), 0),
                func.coalesce(func.sum(LlmCall.tokens_out), 0),
                func.coalesce(func.sum(LlmCall.cost_usd), 0),
            ).where(LlmCall.created_at >= since)
        )
    ).one()
    return (
        int(row[0] or 0),
        int(row[1] or 0),
        int(row[2] or 0),
        int(row[3] or 0),
        int(row[4] or 0),
        float(row[5] or 0),
    )


async def _collect_latency(
    session: AsyncSession, *, since: datetime
) -> LatencyStats:
    """p50/p95/p99 и среднее по `latency_ms` (только не-cache вызовы)."""
    row = (
        await session.execute(
            select(
                func.count(LlmCall.id),
                func.avg(LlmCall.latency_ms),
                func.percentile_cont(0.5).within_group(
                    LlmCall.latency_ms.asc()
                ),
                func.percentile_cont(0.95).within_group(
                    LlmCall.latency_ms.asc()
                ),
                func.percentile_cont(0.99).within_group(
                    LlmCall.latency_ms.asc()
                ),
            ).where(
                LlmCall.created_at >= since,
                LlmCall.cache_hit.is_(False),
            )
        )
    ).one()
    return LatencyStats(
        count=int(row[0] or 0),
        avg_ms=_to_float(row[1]),
        p50_ms=_to_float(row[2]),
        p95_ms=_to_float(row[3]),
        p99_ms=_to_float(row[4]),
    )


async def _collect_grouped(
    session: AsyncSession,
    *,
    column: InstrumentedAttribute[Any],
    since: datetime,
) -> tuple[ModelUsage, ...]:
    """Суммы токенов/стоимости и error/cache-счётчики по колонке."""
    rows = (
        await session.execute(
            select(
                column,
                func.count().label("requests"),
                func.coalesce(func.sum(LlmCall.tokens_in), 0),
                func.coalesce(func.sum(LlmCall.tokens_out), 0),
                func.coalesce(func.sum(LlmCall.cost_usd), 0),
                func.count().filter(LlmCall.status.in_(ERROR_STATUSES)),
                func.count().filter(LlmCall.cache_hit.is_(True)),
            )
            .where(LlmCall.created_at >= since)
            .group_by(column)
            .order_by(func.count().desc(), column)
            .limit(GROUP_LIMIT)
        )
    ).all()
    return tuple(
        ModelUsage(
            key=_group_key(row[0]),
            requests=int(row[1] or 0),
            tokens_in=int(row[2] or 0),
            tokens_out=int(row[3] or 0),
            cost_usd=float(row[4] or 0),
            errors=int(row[5] or 0),
            cache_hits=int(row[6] or 0),
        )
        for row in rows
    )


async def _collect_top_users(
    session: AsyncSession, *, since_date: date
) -> tuple[TopUserRow, ...]:
    """Топ-5 субъектов по сумме токенов из `usage_daily` за окно."""
    token_sum = func.coalesce(func.sum(UsageDaily.tokens_in), 0) + func.coalesce(
        func.sum(UsageDaily.tokens_out), 0
    )
    rows = (
        await session.execute(
            select(
                UsageDaily.subject_type,
                UsageDaily.subject_key,
                func.coalesce(func.sum(UsageDaily.tokens_in), 0),
                func.coalesce(func.sum(UsageDaily.tokens_out), 0),
                func.coalesce(func.sum(UsageDaily.cost_usd), 0),
                func.coalesce(func.sum(UsageDaily.requests), 0),
            )
            .where(UsageDaily.day >= since_date)
            .group_by(UsageDaily.subject_type, UsageDaily.subject_key)
            .order_by(token_sum.desc(), UsageDaily.subject_key)
            .limit(TOP_USERS_LIMIT)
        )
    ).all()
    return tuple(
        TopUserRow(
            subject_type=str(row[0]),
            subject_key=str(row[1]),
            tokens_in=int(row[2] or 0),
            tokens_out=int(row[3] or 0),
            cost_usd=float(row[4] or 0),
            requests=int(row[5] or 0),
        )
        for row in rows
    )


async def _collect_period(
    session: AsyncSession, *, period: Period, since: datetime
) -> PeriodMetrics:
    """Собрать сводку за окно."""
    requests, errors, cache_hits, tokens_in, tokens_out, cost = (
        await _collect_totals(session, since=since)
    )
    latency = await _collect_latency(session, since=since)
    by_model = await _collect_grouped(
        session, column=LlmCall.model, since=since
    )
    by_level = await _collect_grouped(
        session, column=LlmCall.level, since=since
    )
    return PeriodMetrics(
        period=period,
        since=since,
        requests=requests,
        errors=errors,
        cache_hits=cache_hits,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency=latency,
        by_model=by_model,
        by_level=by_level,
    )


async def collect_dashboard(
    session: AsyncSession, *, now: datetime | None = None
) -> DashboardData:
    """Собрать все метрики дашборда (3 окна + top-5 пользователей)."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    periods: list[PeriodMetrics] = []
    top_users: dict[str, tuple[TopUserRow, ...]] = {}
    for period in PERIODS:
        since = moment - period.delta
        periods.append(
            await _collect_period(session, period=period, since=since)
        )
        # `usage_daily` — дневная гранулярность: берём день начала окна.
        top_users[period.key] = await _collect_top_users(
            session, since_date=since.date()
        )
    return DashboardData(
        generated_at=moment,
        periods=tuple(periods),
        top_users=top_users,
    )


class LlmOpsDashboardView(CustomView):
    """Страница «LLMOps-дашборд» в админке (T-109).

    Read-only: только SELECT-агрегаты по `llm_calls`/`usage_daily`, запись
    не выполняется. Доступна и `admin`, и `auditor` (guard пропускает GET);
    для `user` guard вернёт 403 ещё до view.
    """

    menu_label = "LLMOps-дашборд"
    icon = "fa fa-gauge-high"
    path = "/llmops"
    route_name = "llmops-dashboard"

    @route("")
    async def index(self, request: Request) -> Response:
        """Отрендерить страницу с агрегатами за 24h/7d/30d."""
        templates = self.templates
        if templates is None:  # pragma: no cover - выставляется при mount
            raise RuntimeError("templates не инициализированы")
        async with SessionLocal() as session:
            data = await collect_dashboard(session)
        return templates.TemplateResponse(
            request=request,
            name="llmops_dashboard.html",
            context={"title": "LLMOps-дашборд", "data": data},
        )
