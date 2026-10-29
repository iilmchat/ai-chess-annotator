"""Retention `export_jobs` + `EXPORT_DIR`: файлы и строки старше N дней (KI-054).

Фоновые выгрузки датасета (KI-050/KI-053) пишут готовый JSONL в
``EXPORT_DIR`` и строку в `export_jobs`. Без уборки оба хранилища растут
бесконечно, поэтому по умолчанию храним только последние
``EXPORT_RETENTION_DAYS`` дней (7).

Алгоритм:

1. выбрать батч джоб с ``created_at < cutoff`` (id по возрастанию);
2. для каждой джобы удалить файл(ы) **только внутри** `EXPORT_DIR`
   (`file_path` из БД и канонический ``edits-<id>.jsonl``); путь вне
   корня не трогается — это защита от подмены `file_path` в БД;
3. удалить строки батча и закоммитить;
4. повторять, пока есть старые строки (или не сработал ``--max-batches``).

Почему батчами: короткая транзакция не держит блокировки и раздувает WAL
меньше, чем один большой ``DELETE``; между батчами есть пауза.

Почему файлы удаляются до строк: если процесс упадёт между шагами, в БД
останется строка без файла (download отдаст 410), а не наоборот «файл
есть, но о нём никто не знает» (диск не освободится никогда). Ошибка
удаления файла логируется, но не блокирует retention строки.

Запуск:

    python scripts/prune_export_jobs.py                    # 7 дней из настроек
    python scripts/prune_export_jobs.py --retention-days 30
    python scripts/prune_export_jobs.py --export-dir /srv/data/exports
    python scripts/prune_export_jobs.py --batch-size 50 --sleep-ms 100
    python scripts/prune_export_jobs.py --dry-run --json

``--dry-run`` считает строки/файлы, ничего не удаляя; ``--json`` печатает
машинный отчёт. Exit codes: ``0`` — успех (в т.ч. когда удалять нечего);
``2`` — неверные аргументы или БД недоступна.

Планирование (cron/планировщик) — ``docs/development/GETTING_STARTED.md``
§9.3.
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

import structlog  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.models import ExportJob  # noqa: E402

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_RETENTION_DAYS",
    "PruneStats",
    "build_parser",
    "prune_batches",
    "resolve_cutoff",
]

logger = structlog.get_logger(__name__)

#: Сколько дней хранить файлы и строки `export_jobs` по умолчанию (KI-054;
#: совпадает с `Settings.export_retention_days`).
DEFAULT_RETENTION_DAYS = 7

#: Сколько джоб разбирать за одну транзакцию.
DEFAULT_BATCH_SIZE = 100

#: Коды возврата CLI.
EXIT_OK = 0
EXIT_ERROR = 2


@dataclass(frozen=True, slots=True)
class PruneStats:
    """Результат одного запуска retention экспортов."""

    retention_days: int
    cutoff: datetime
    batch_size: int
    batches: int
    deleted_rows: int
    deleted_files: int
    missing_files: int
    skipped_files: int
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
            "deleted_rows": self.deleted_rows,
            "deleted_files": self.deleted_files,
            "missing_files": self.missing_files,
            "skipped_files": self.skipped_files,
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
            всей таблицы и всех файлов).
    """
    if retention_days < 1:
        raise ValueError("retention_days должен быть >= 1")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment - timedelta(days=retention_days)


def _candidate_paths(
    job: ExportJob, export_dir: Path
) -> tuple[list[Path], list[Path]]:
    """Файлы джобы: (безопасные внутри `EXPORT_DIR`, опасные снаружи).

    `file_path` приходит из БД и может быть подменён, поэтому удаляем
    только пути внутри `EXPORT_DIR`. Канонический ``edits-<id>.jsonl``
    добавляется всегда: он строится из числового id и не может выйти за
    пределы корня (на случай legacy-строк без `file_path`).
    """
    safe: set[Path] = set()
    unsafe: set[Path] = set()
    raw = job.file_path
    if raw:
        try:
            candidate = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            candidate = None
        if candidate is not None:
            if candidate.is_relative_to(export_dir):
                safe.add(candidate)
            else:
                unsafe.add(candidate)
    canonical_raw = export_dir / f"edits-{int(job.id)}.jsonl"
    try:
        canonical = canonical_raw.resolve()
    except (OSError, RuntimeError):
        # Битый симлинк/петля: удалять небезопасно.
        unsafe.add(canonical_raw)
    else:
        if canonical.is_relative_to(export_dir):
            safe.add(canonical)
        else:
            # Симлинк `edits-<id>.jsonl` может указывать за пределы EXPORT_DIR.
            unsafe.add(canonical)
    return sorted(safe), sorted(unsafe)


def _resolve_export_dir(raw: str) -> Path:
    """Нормализованный абсолютный путь `EXPORT_DIR` (без создания)."""
    return Path(raw).expanduser().resolve()


async def prune_batches(
    session: AsyncSession,
    *,
    retention_days: int,
    export_dir: Path | str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    cutoff: datetime | None = None,
    max_batches: int | None = None,
    sleep_s: float = 0.0,
    dry_run: bool = False,
) -> PruneStats:
    """Удалить старые `export_jobs` и их файлы батчами.

    Args:
        session: сессия БД; коммиты делает функция.
        retention_days: сколько дней хранить.
        export_dir: корень готовых JSONL; файлы вне него не удаляются.
        batch_size: максимум джоб в одном батче (≥ 1).
        cutoff: явная граница (тесты/повторный запуск); по умолчанию —
            ``now - retention_days``.
        max_batches: ограничение числа батчей за запуск (cron-гигиена).
        sleep_s: пауза между батчами.
        dry_run: только посчитать строки/файлы, ничего не удалять.

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
    root = _resolve_export_dir(str(export_dir))
    border = cutoff if cutoff is not None else resolve_cutoff(retention_days)

    if dry_run:
        jobs = list(
            await session.scalars(
                select(ExportJob)
                .where(ExportJob.created_at < border)
                .order_by(ExportJob.id)
            )
        )
        existing = 0
        missing = 0
        skipped = 0
        for job in jobs:
            safe, unsafe = _candidate_paths(job, root)
            skipped += len(unsafe)
            for path in safe:
                if path.is_file():
                    existing += 1
                else:
                    missing += 1
        return PruneStats(
            retention_days=retention_days,
            cutoff=border,
            batch_size=batch_size,
            batches=0,
            deleted_rows=len(jobs),
            deleted_files=existing,
            missing_files=missing,
            skipped_files=skipped,
            dry_run=True,
        )

    deleted_rows = 0
    deleted_files = 0
    missing_files = 0
    skipped_files = 0
    batches = 0
    capped = False

    while True:
        if max_batches is not None and batches >= max_batches:
            capped = True
            break
        jobs = list(
            await session.scalars(
                select(ExportJob)
                .where(ExportJob.created_at < border)
                .order_by(ExportJob.id)
                .limit(batch_size)
            )
        )
        if not jobs:
            break

        for job in jobs:
            safe, unsafe = _candidate_paths(job, root)
            for path in unsafe:
                skipped_files += 1
                logger.warning(
                    "export_prune_path_outside_root",
                    job_id=job.id,
                    path=str(path),
                    root=str(root),
                )
            for path in safe:
                if not path.is_file():
                    missing_files += 1
                    continue
                try:
                    await asyncio.to_thread(path.unlink)
                    deleted_files += 1
                except FileNotFoundError:
                    missing_files += 1
                except OSError as exc:  # права/занятый файл: строку всё равно чистим
                    skipped_files += 1
                    logger.warning(
                        "export_prune_file_remove_failed",
                        job_id=job.id,
                        path=str(path),
                        error=str(exc),
                    )

        job_ids = [job.id for job in jobs]
        result = await session.execute(
            delete(ExportJob)
            .where(ExportJob.id.in_(job_ids))
            .execution_options(synchronize_session=False)
        )
        removed = int(getattr(result, "rowcount", 0) or 0)
        await session.commit()
        batches += 1
        deleted_rows += removed
        # Хвост: за батч выбрали меньше лимита — старых джоб больше нет.
        if len(jobs) < batch_size:
            break
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

    return PruneStats(
        retention_days=retention_days,
        cutoff=border,
        batch_size=batch_size,
        batches=batches,
        deleted_rows=deleted_rows,
        deleted_files=deleted_files,
        missing_files=missing_files,
        skipped_files=skipped_files,
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
    default_days = max(1, int(settings.export_retention_days))
    parser = argparse.ArgumentParser(
        prog="prune_export_jobs.py",
        description=(
            "Retention export_jobs и EXPORT_DIR: удаление строк и файлов "
            "старше N дней (KI-054)."
        ),
    )
    parser.add_argument(
        "--retention-days",
        type=_positive_int,
        default=default_days,
        metavar="N",
        help=(
            "хранить экспорты за последние N дней "
            f"(по умолчанию {default_days} = EXPORT_RETENTION_DAYS)"
        ),
    )
    parser.add_argument(
        "--export-dir",
        default=settings.export_dir,
        metavar="PATH",
        help=(
            "каталог готовых JSONL "
            f"(по умолчанию {settings.export_dir} = EXPORT_DIR)"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        metavar="N",
        help=f"джоб в одном батче (по умолчанию {DEFAULT_BATCH_SIZE})",
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
        help="только посчитать строки/файлы старше cutoff, ничего не удалять",
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
        f"Retention export_jobs (последние {stats.retention_days} дн., "
        f"cutoff {stats.cutoff_iso}):"
    )
    print(f"  {verb}: {stats.deleted_rows} строк")
    print(f"  {verb}: {stats.deleted_files} файлов")
    if stats.missing_files:
        print(f"  отсутствовало файлов: {stats.missing_files}")
    if stats.skipped_files:
        print(
            "  пропущено файлов (вне EXPORT_DIR/ошибка удаления): "
            f"{stats.skipped_files}"
        )
    if not stats.dry_run:
        print(f"  батчей: {stats.batches} (по {stats.batch_size})")
    if stats.capped:
        print(
            "  остановлено по --max-batches: остались строки для "
            "следующего запуска"
        )


async def _prune(args: argparse.Namespace) -> int:
    """Выполнить retention; вернуть exit code (0/2)."""
    max_batches = args.max_batches or None
    export_dir = _resolve_export_dir(str(args.export_dir))
    engine = create_async_engine(
        get_settings().database_url, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            stats = await prune_batches(
                session,
                retention_days=args.retention_days,
                export_dir=export_dir,
                batch_size=args.batch_size,
                max_batches=max_batches,
                sleep_s=args.sleep_ms / 1000.0,
                dry_run=args.dry_run,
            )
    except Exception as exc:  # БД недоступна/битая схема — понятная ошибка
        print(f"Ошибка БД при очистке export_jobs: {exc}", file=sys.stderr)
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
