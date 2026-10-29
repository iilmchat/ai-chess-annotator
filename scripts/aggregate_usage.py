"""Месячные FinOps-агрегаты из durable `usage_daily` (T-031b).

Скрипт читает durable-агрегаты (их пишет `app.finops.record_token_usage`
best-effort upsert-ом) и печатает суммы по типу субъекта и по дням, а также
чистит устаревшие строки (retention).

Запуск:

    python scripts/aggregate_usage.py                      # текущий месяц UTC
    python scripts/aggregate_usage.py --month 2026-10
    python scripts/aggregate_usage.py --month 2026-10 --json
    python scripts/aggregate_usage.py --retention-days 30
    python scripts/aggregate_usage.py --no-cleanup

Exit codes: ``0`` — успех; ``2`` — неверные аргументы или БД недоступна.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import UsageDaily  # noqa: E402

__all__ = [
    "MonthReport",
    "UsageTotals",
    "build_parser",
    "collect_month",
    "main",
    "month_range",
    "parse_month",
]

#: Сколько дней durable-агрегатов хранить по умолчанию (DESIGN §4.4/4.5).
DEFAULT_RETENTION_DAYS = 90

#: Коды возврата CLI.
EXIT_OK = 0
EXIT_ERROR = 2


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """Суммы расхода за период/срез."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")
    requests: int = 0

    @property
    def tokens_total(self) -> int:
        """Суммарные токены (in + out)."""
        return self.tokens_in + self.tokens_out

    def as_dict(self) -> dict[str, int | float]:
        """JSON-совместимое представление (cost — float с 6 знаками)."""
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": self.tokens_total,
            "cost_usd": round(float(self.cost_usd), 6),
            "requests": self.requests,
        }


@dataclass(frozen=True, slots=True)
class MonthReport:
    """Агрегаты за месяц: итоги, срезы по типу субъекта и по дням."""

    month: str
    start: date
    end: date
    totals: UsageTotals
    by_subject_type: list[dict[str, object]]
    by_day: list[dict[str, object]]
    retention: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        """JSON-совместимое представление отчёта."""
        return {
            "month": self.month,
            "from": self.start.isoformat(),
            "to_exclusive": self.end.isoformat(),
            "totals": self.totals.as_dict(),
            "by_subject_type": self.by_subject_type,
            "by_day": self.by_day,
            "retention": self.retention,
        }


def month_range(year: int, month: int) -> tuple[date, date]:
    """Границы месяца: ``[первое число, первое число следующего)``."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def parse_month(value: str) -> tuple[date, date]:
    """Разобрать ``YYYY-MM`` в границы месяца.

    Raises:
        ValueError: формат не ``YYYY-MM`` или месяц вне 1..12.
    """
    parts = value.split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError(
            f"неверный месяц {value!r}: ожидается формат YYYY-MM"
        )
    try:
        year = int(parts[0])
        month = int(parts[1])
    except ValueError as exc:
        raise ValueError(
            f"неверный месяц {value!r}: ожидается формат YYYY-MM"
        ) from exc
    if not 1 <= month <= 12:
        raise ValueError(f"неверный месяц {value!r}: месяц должен быть 1..12")
    return month_range(year, month)


def _month_arg(value: str) -> tuple[date, date]:
    """argparse-тип ``--month`` (ошибка → exit 2 с понятным текстом)."""
    try:
        return parse_month(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _non_negative_int(value: str) -> int:
    """argparse-тип для ``--retention-days`` (≥ 0)."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число дней, получено {value!r}"
        ) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("дни retention должны быть ≥ 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер скрипта."""
    start, _end = month_range(*_current_year_month())
    parser = argparse.ArgumentParser(
        prog="aggregate_usage.py",
        description="Месячные FinOps-агрегаты из usage_daily (T-031b).",
    )
    parser.add_argument(
        "--month",
        type=_month_arg,
        default=(start, month_range(start.year, start.month)[1]),
        metavar="YYYY-MM",
        help="месяц отчёта (по умолчанию — текущий месяц UTC)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="машинный вывод (JSON)",
    )
    parser.add_argument(
        "--retention-days",
        type=_non_negative_int,
        default=DEFAULT_RETENTION_DAYS,
        metavar="N",
        help=(
            "удалить строки usage_daily старше N дней "
            f"(по умолчанию {DEFAULT_RETENTION_DAYS})"
        ),
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="не удалять устаревшие строки (только отчёт)",
    )
    return parser


def _current_year_month(now: datetime | None = None) -> tuple[int, int]:
    """Текущий год и месяц в UTC (default для ``--month``)."""
    moment = now or datetime.now(UTC)
    return moment.year, moment.month


async def collect_month(
    session: AsyncSession,
    *,
    start: date,
    end: date,
    retention_days: int | None,
) -> MonthReport:
    """Собрать отчёт за ``[start, end)`` и (опционально) почистить старое."""
    totals = await _totals(session, start=start, end=end)
    by_subject_type = await _by_subject_type(session, start=start, end=end)
    by_day = await _by_day(session, start=start, end=end)
    retention: dict[str, object] | None = None
    if retention_days is not None:
        retention = await _cleanup_old(session, retention_days=retention_days)
    return MonthReport(
        month=f"{start.year:04d}-{start.month:02d}",
        start=start,
        end=end,
        totals=totals,
        by_subject_type=by_subject_type,
        by_day=by_day,
        retention=retention,
    )


async def _totals(
    session: AsyncSession, *, start: date, end: date
) -> UsageTotals:
    """Суммарные токены/стоимость/запросы за период."""
    row = (
        await session.execute(
            _sum_columns().where(
                UsageDaily.day >= start, UsageDaily.day < end
            )
        )
    ).one()
    return UsageTotals(
        tokens_in=int(row[0] or 0),
        tokens_out=int(row[1] or 0),
        cost_usd=Decimal(row[2] or 0),
        requests=int(row[3] or 0),
    )


async def _by_subject_type(
    session: AsyncSession, *, start: date, end: date
) -> list[dict[str, object]]:
    """Срез по типу субъекта (`user`/`ip`) с числом уникальных субъектов."""
    rows = (
        await session.execute(
            select(
                UsageDaily.subject_type,
                *_sum_expressions(),
                func.count(func.distinct(UsageDaily.subject_key)),
            )
            .where(UsageDaily.day >= start, UsageDaily.day < end)
            .group_by(UsageDaily.subject_type)
            .order_by(UsageDaily.subject_type)
        )
    ).all()
    return [
        {
            "subject_type": str(row[0]),
            **UsageTotals(
                tokens_in=int(row[1] or 0),
                tokens_out=int(row[2] or 0),
                cost_usd=Decimal(row[3] or 0),
                requests=int(row[4] or 0),
            ).as_dict(),
            "subjects": int(row[5] or 0),
        }
        for row in rows
    ]


async def _by_day(
    session: AsyncSession, *, start: date, end: date
) -> list[dict[str, object]]:
    """Срез по дням месяца."""
    rows = (
        await session.execute(
            select(UsageDaily.day, *_sum_expressions())
            .where(UsageDaily.day >= start, UsageDaily.day < end)
            .group_by(UsageDaily.day)
            .order_by(UsageDaily.day)
        )
    ).all()
    return [
        {
            "day": _day_iso(row[0]),
            **UsageTotals(
                tokens_in=int(row[1] or 0),
                tokens_out=int(row[2] or 0),
                cost_usd=Decimal(row[3] or 0),
                requests=int(row[4] or 0),
            ).as_dict(),
        }
        for row in rows
    ]


async def _cleanup_old(
    session: AsyncSession, *, retention_days: int
) -> dict[str, object]:
    """Удалить `usage_daily` старше retention; вернуть статистику."""
    cutoff = datetime.now(UTC).date() - timedelta(days=retention_days)
    result = await session.execute(
        delete(UsageDaily).where(UsageDaily.day < cutoff)
    )
    await session.commit()
    deleted = int(getattr(result, "rowcount", 0) or 0)
    return {
        "retention_days": retention_days,
        "cutoff": cutoff.isoformat(),
        "deleted": deleted,
    }


def _sum_expressions() -> tuple[
    object,
    object,
    object,
    object,
]:
    """Четыре SUM-выражения (in/out/cost/requests) с COALESCE."""
    return (
        func.coalesce(func.sum(UsageDaily.tokens_in), 0),
        func.coalesce(func.sum(UsageDaily.tokens_out), 0),
        func.coalesce(func.sum(UsageDaily.cost_usd), 0),
        func.coalesce(func.sum(UsageDaily.requests), 0),
    )


def _sum_columns() -> object:
    """SELECT с четырьмя SUM-выражениями."""
    return select(*_sum_expressions())


def _day_iso(value: object) -> str:
    """Дата из БД → ISO-строка (SQLAlchemy возвращает `date`)."""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


async def _run(args: argparse.Namespace) -> int:
    """Выполнить отчёт; вернуть exit code (0/2)."""
    start, end = args.month
    retention_days = None if args.no_cleanup else args.retention_days
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await collect_month(
                session,
                start=start,
                end=end,
                retention_days=retention_days,
            )
    except Exception as exc:  # БД недоступна/битая схема — понятная ошибка
        print(f"Ошибка БД при чтении usage_daily: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return EXIT_OK


def _print_report(report: MonthReport) -> None:
    """Человекочитаемый отчёт в stdout."""
    totals = report.totals
    print(
        f"FinOps usage за {report.month} "
        f"({report.start.isoformat()}..{report.end.isoformat()}):"
    )
    print(
        "  токены: "
        f"in={totals.tokens_in}, out={totals.tokens_out}, "
        f"всего={totals.tokens_total}"
    )
    print(f"  стоимость: ${float(totals.cost_usd):.6f}")
    print(f"  запросов: {totals.requests}")
    if report.by_subject_type:
        print("По типу субъекта:")
        for item in report.by_subject_type:
            print(
                f"  {item['subject_type']}: "
                f"tokens={item['tokens_total']}, "
                f"cost=${float(item['cost_usd']):.6f}, "  # type: ignore[arg-type]
                f"requests={item['requests']}, "
                f"subjects={item['subjects']}"
            )
    else:
        print("По типу субъекта: данных нет")
    if report.by_day:
        print(f"По дням ({len(report.by_day)}):")
        for item in report.by_day:
            print(
                f"  {item['day']}: tokens={item['tokens_total']}, "
                f"cost=${float(item['cost_usd']):.6f}, "  # type: ignore[arg-type]
                f"requests={item['requests']}"
            )
    else:
        print("По дням: данных нет")
    if report.retention is not None:
        print(
            "Retention: удалено "
            f"{report.retention['deleted']} строк старше "
            f"{report.retention['retention_days']} дн. "
            f"(cutoff {report.retention['cutoff']})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: вернуть exit code (argparse сам даёт 2 на ошибках)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
