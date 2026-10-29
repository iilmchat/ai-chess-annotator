"""Retention `llm_calls`: батчевая очистка старых вызовов LLM (KI-048).

`llm_calls` (T-030) — append-only durable-история вызовов LLM. Она растёт
неограниченно, поэтому по умолчанию храним только последние
``LLM_CALLS_RETENTION_DAYS`` дней (90).

Почему батчами, а не одним ``DELETE``:

* один большой ``DELETE`` держит блокировки дольше и раздувает WAL;
* короткие транзакции по ``--batch-size`` строк (по умолчанию 1000) дают
  autovacuum'у шанс отработать между батчами и не блокируют запись новых
  вызовов;
* между батчами — небольшая пауза (``--sleep-ms``), чтобы не занимать
  соединение и дисковый I/O на 100%.

Запуск:

    python scripts/prune_llm_calls.py                  # 90 дней из настроек
    python scripts/prune_llm_calls.py --retention-days 30
    python scripts/prune_llm_calls.py --batch-size 500 --sleep-ms 100
    python scripts/prune_llm_calls.py --max-batches 10
    python scripts/prune_llm_calls.py --dry-run --json

``--dry-run`` только считает строки старше cutoff; ``--json`` печатает
машинный отчёт. Exit codes: ``0`` — успех (в т.ч. когда удалять нечего);
``2`` — неверные аргументы или БД недоступна.

Планирование (cron/планировщик) описано в
``docs/development/GETTING_STARTED.md`` §9.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from app.db.models import LlmCall  # noqa: E402

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_RETENTION_DAYS",
    "PruneStats",
    "build_parser",
    "count_expired",
    "prune_batches",
    "resolve_cutoff",
]

#: Сколько дней хранить `llm_calls` по умолчанию (KI-048; совпадает с
#: `Settings.llm_calls_retention_days`).
DEFAULT_RETENTION_DAYS = 90

#: Сколько строк удалять за одну транзакцию.
DEFAULT_BATCH_SIZE = 1000

#: Коды возврата CLI.
EXIT_OK = 0
EXIT_ERROR = 2


@dataclass(frozen=True, slots=True)
class PruneStats:
    """Результат одного запуска retention."""

    retention_days: int
    cutoff: datetime
    batch_size: int
    batches: int
    deleted: int
    dry_run: bool = False
    capped: bool = False

    @property
    def cutoff_iso(self) -> str:
        """Cutoff в ISO-8601 (UTC)."""
        return self.cutoff.isoformat()

    def as_dict(self) -> dict[str, object]:
        """JSON-совместимое представление отчёта."""
        return {
            "retention_days": self.retention_days,
            "cutoff": self.cutoff_iso,
            "batch_size": self.batch_size,
            "batches": self.batches,
            "deleted": self.deleted,
            "dry_run": self.dry_run,
            "capped": self.capped,
        }


def resolve_cutoff(
    retention_days: int, *, now: datetime | None = None
) -> datetime:
    """Граница retention: всё, что старше ``now - retention_days``.

    ``now`` подставляется в тестах; по умолчанию — текущее время UTC.

    Raises:
        ValueError: ``retention_days`` < 1 (защита от случайного удаления
            всей таблицы).
    """
    if retention_days < 1:
        raise ValueError("retention_days должен быть >= 1")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment - timedelta(days=retention_days)


async def count_expired(
    session: AsyncSession, *, cutoff: datetime
) -> int:
    """Посчитать строки старше cutoff (для ``--dry-run``)."""
    total = await session.scalar(
        select(func.count())
        .select_from(LlmCall)
        .where(LlmCall.created_at < cutoff)
    )
    return int(total or 0)


async def prune_batches(
    session: AsyncSession,
    *,
    retention_days: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cutoff: datetime | None = None,
    max_batches: int | None = None,
    sleep_s: float = 0.0,
    dry_run: bool = False,
) -> PruneStats:
    """Удалить старые `llm_calls` батчами, коммитя каждый батч.

    Один батч — один ``DELETE ... WHERE id IN (SELECT id ... LIMIT N)``:
    объём удаляемого ограничен, транзакция короткая, автовакуум успевает
    между итерациями. Цикл останавливается, когда удалено меньше
    ``batch_size`` (хвост), либо исчерпан ``max_batches``.

    Args:
        session: сессия БД; коммиты делает функция.
        retention_days: сколько дней хранить.
        batch_size: максимум строк в одном DELETE (≥ 1).
        cutoff: явная граница (тесты/повторный запуск); по умолчанию —
            ``now - retention_days``.
        max_batches: ограничение числа батчей за запуск (cron-гигиена).
        sleep_s: пауза между батчами.
        dry_run: только посчитать, ничего не удалять.

    Returns:
        Статистика удаления.

    Raises:
        ValueError: неверные ``retention_days``/``batch_size``/
            ``max_batches``.
    """
    if batch_size < 1:
        raise ValueError("batch_size должен быть >= 1")
    if max_batches is not None and max_batches < 1:
        raise ValueError("max_batches должен быть >= 1")
    border = cutoff if cutoff is not None else resolve_cutoff(retention_days)

    if dry_run:
        expired = await count_expired(session, cutoff=border)
        return PruneStats(
            retention_days=retention_days,
            cutoff=border,
            batch_size=batch_size,
            batches=0,
            deleted=expired,
            dry_run=True,
        )

    deleted = 0
    batches = 0
    capped = False
    while True:
        if max_batches is not None and batches >= max_batches:
            capped = True
            break
        expired_ids = (
            select(LlmCall.id)
            .where(LlmCall.created_at < border)
            .order_by(LlmCall.id)
            .limit(batch_size)
            .scalar_subquery()
        )
        result = await session.execute(
            delete(LlmCall)
            .where(LlmCall.id.in_(expired_ids))
            .execution_options(synchronize_session=False)
        )
        removed = int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
        batches += 1
        deleted += removed
        # Хвост: за один батч удалили меньше лимита — больше старых строк нет.
        if removed < batch_size:
            break
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

    return PruneStats(
        retention_days=retention_days,
        cutoff=border,
        batch_size=batch_size,
        batches=batches,
        deleted=deleted,
        capped=capped,
    )


def _positive_int(value: str) -> int:
    """argparse-тип для ``--retention-days``/``--batch-size`` (≥ 1)."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("значение должно быть >= 1")
    return parsed


def _non_negative_int(value: str) -> int:
    """argparse-тип для ``--max-batches``/``--sleep-ms`` (≥ 0)."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("значение должно быть >= 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер скрипта."""
    settings = get_settings()
    default_days = max(1, int(settings.llm_calls_retention_days))
    parser = argparse.ArgumentParser(
        prog="prune_llm_calls.py",
        description=(
            "Retention llm_calls: батчевое удаление вызовов LLM старше N дней "
            "(KI-048)."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=_positive_int,
        default=default_days,
        metavar="N",
        help=(
            "хранить вызовы за последние N дней "
            f"(по умолчанию {default_days} = LLM_CALLS_RETENTION_DAYS)"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"строк в одном DELETE (по умолчанию {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--max-batches",
        type=_non_negative_int,
        default=None,
        metavar="N",
        help="не более N батчей за запуск (0 — без ограничения)",
    )
    parser.add_argument(
        "--sleep-ms",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help="пауза между батчами в миллисекундах (по умолчанию 0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только посчитать строки старше cutoff, ничего не удалять",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="машинный вывод (JSON)",
    )
    return parser


def _print_report(stats: PruneStats) -> None:
    """Человекочитаемый отчёт в stdout."""
    verb = "будет удалено" if stats.dry_run else "удалено"
    print(
        f"Retention llm_calls (последние {stats.retention_days} дн., "
        f"cutoff {stats.cutoff_iso}):"
    )
    print(f"  {verb}: {stats.deleted} строк")
    if not stats.dry_run:
        print(f"  батчей: {stats.batches} (по {stats.batch_size})")
    if stats.capped:
        print("  остановлено по --max-batches: остались строки для следующего запуска")


async def _prune(args: argparse.Namespace) -> int:
    """Выполнить retention; вернуть exit code (0/2)."""
    max_batches = args.max_batches or None
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            stats = await prune_batches(
                session,
                retention_days=args.retention_days,
                batch_size=args.batch_size,
                max_batches=max_batches,
                sleep_s=args.sleep_ms / 1000.0,
                dry_run=args.dry_run,
            )
    except Exception as exc:  # БД недоступна/битая схема — понятная ошибка
        print(f"Ошибка БД при очистке llm_calls: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))
    else:
        _print_report(stats)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: вернуть exit code (argparse сам даёт 2 на ошибках)."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    # 0 в --max-batches означает «без ограничения» (см. help).
    if args.max_batches == 0:
        args.max_batches = None
    return asyncio.run(_prune(args))


if __name__ == "__main__":
    raise SystemExit(main())
