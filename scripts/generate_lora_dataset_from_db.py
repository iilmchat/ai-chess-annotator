"""Сборка v2 JSONL-датасета для LoRA из PostgreSQL (T-080, итерация 39).

Скрипт готовит данные для ``scripts/train_lora.py`` на GPU-машине: читает
``edits`` (правки редакторов), подтягивает к ним ``commentaries`` и
``games``, восстанавливает анализ ходов и собирает ровно ту же v2-запись,
что и ``scripts/export_dataset.py`` (``schema_version=2``):

* ``system`` — системный промпт уровня из ``app/prompts/<level>.txt``;
* ``user`` — пакет фактов (``build_fact_package``);
* ``assistant`` — принятая правка редактора (``edits.edited_text``).

Контракт записи не дублируется: используются ``build_fact_sheet`` и
``record_from_edit`` из ``scripts.export_dataset`` (реэкспорт из
``app.dataset.export``). Поэтому файл читается ``train_lora.py`` без
конвертации.

**Выборка.** Кандидаты — только комментарии, у которых есть хотя бы одна
правка (inner join ``edits`` → ``commentaries``, как в ``export_dataset``):
учить модель нечему, если нет ``assistant``-ответа. Порядок — стабильный:
``edits.created_at DESC, edits.id DESC`` (сначала свежие правки). Берётся
окно последних ``limit * CANDIDATE_FETCH_FACTOR`` кандидатов, затем
``--seed`` детерминированно перемешивает его, а ``--limit`` ограничивает
число записей. Без ``--seed`` сохраняется порядок «свежие → старые».

**Пропуски.** Записи без партии, с битым PGN, без анализа ходов или с
неизвестным уровнем не попадают в файл: причина учитывается в счётчике и
печатается в итоговой сводке. Скрипт не падает на одной плохой записи.

**Exit codes.** ``0`` — успех (хотя бы одна запись); ``2`` — неверные
аргументы, пустая БД/нет партий с комментариями или ошибка подключения.

Запуск::

    python scripts/generate_lora_dataset_from_db.py --dry-run --limit 500
    python scripts/generate_lora_dataset_from_db.py \\
        --limit 500 --level club --output data/lora_club_v1.jsonl
    python scripts/generate_lora_dataset_from_db.py --seed 42

Для тестов ``main()`` принимает ``session_factory`` и ``record_builder``:
CLI проверяется без PostgreSQL (unit-тесты подставляют фейковую сессию).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.commentary.service import load_analyses  # noqa: E402
from app.dataset.export import level_value  # noqa: E402
from app.db.models import Commentary, Edit, Game, Level  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.engine.models import MoveAnalysis  # noqa: E402
from app.errors import InvalidLevelError, InvalidPgnError  # noqa: E402
from app.ingestion import ParsedGame, parse_pgn  # noqa: E402
from scripts import export_dataset  # noqa: E402

__all__ = [
    "CANDIDATE_FETCH_FACTOR",
    "DEFAULT_LIMIT",
    "DEFAULT_OUTPUT",
    "EXTRA_SKIP_REASON_LABELS",
    "LEVELS",
    "SCHEMA_VERSION",
    "SKIP_REASON_LABELS",
    "Candidate",
    "DatasetConfig",
    "GenerationResult",
    "GenerationStats",
    "RecordBuilder",
    "RecordSkippedError",
    "SessionFactory",
    "build_parser",
    "build_record",
    "build_record_from_db",
    "count_words",
    "filter_candidates",
    "load_candidates",
    "main",
    "run_export",
    "sample_records",
    "write_records",
]

#: Актуальная схема JSONL (см. ``scripts/export_dataset.py``).
SCHEMA_VERSION = export_dataset.SCHEMA_VERSION
#: Целевое число записей по умолчанию (столько же в `docs` примерах).
DEFAULT_LIMIT = 500
#: Куда писать датасет по умолчанию.
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_club_v1.jsonl"
#: Уровни, поддерживаемые промптами.
LEVELS: tuple[str, ...] = tuple(level.value for level in Level)
#: Во сколько раз больше кандидатов читать из БД, чем нужно записей.
#: Запас нужен для пропусков (нет анализа/битый PGN) и фильтра `--min-words`.
CANDIDATE_FETCH_FACTOR = 5
#: Верхняя граница строк, читаемых одним запросом (защита от гигантской БД).
MAX_CANDIDATE_ROWS = 20_000
#: Причина пропуска, которой нет в `export_dataset` (локальный фильтр).
EXTRA_SKIP_REASON_LABELS: dict[str, str] = {
    "min_words": "правка короче --min-words слов",
}
#: Все подписи причин пропуска для итоговой сводки.
SKIP_REASON_LABELS: dict[str, str] = {
    **export_dataset.SKIP_REASON_LABELS,
    **EXTRA_SKIP_REASON_LABELS,
}

#: Строка БД: правка, её комментарий и (возможно отсутствующая) партия.
Candidate = tuple[Edit, Commentary, Game | None]

#: Фабрика сессий; в тестах подменяется на фейковую (без PostgreSQL).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class RecordSkippedError(Exception):
    """Запись нельзя экспортировать; ``reason`` — ключ ``SKIP_REASON_LABELS``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class RecordBuilder(Protocol):
    """Сборщик одной v2-записи из строк БД (точка внедрения для тестов)."""

    async def __call__(
        self,
        session: AsyncSession,
        edit: Edit,
        commentary: Commentary,
        game: Game | None,
        *,
        min_words: int = 0,
    ) -> dict[str, Any]:
        """Вернуть запись или поднять ``RecordSkippedError`` с причиной."""
        ...


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    """Параметры одного прогона сборки датасета.

    Attributes:
        output: путь JSONL (родительский каталог создаётся).
        limit: максимум записей в файле (и размер целевой выборки).
        level: ``beginner``/``club``/``gm`` или ``None`` — все уровни.
        min_words: минимальное число слов в правке (0 — без фильтра).
        seed: seed детерминированной выборки; ``None`` — без перемешивания.
        dry_run: посчитать статистику, но не писать файл.
    """

    output: Path = DEFAULT_OUTPUT
    limit: int = DEFAULT_LIMIT
    level: str | None = None
    min_words: int = 0
    seed: int | None = None
    dry_run: bool = False


@dataclass
class GenerationStats:
    """Счётчики прогона: сколько кандидатов, записей и почему пропуски."""

    limit: int = DEFAULT_LIMIT
    candidates: int = 0
    sampled: int = 0
    exported: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    levels: Counter[str] = field(default_factory=Counter)

    @property
    def skipped_total(self) -> int:
        """Всего пропущенных записей."""
        return sum(self.skipped.values())

    def record_exported(self, level: str) -> None:
        """Учесть успешно собранную запись."""
        self.exported += 1
        self.levels[level] += 1

    def record_skipped(self, reason: str) -> None:
        """Учесть пропущенную запись (причина — ключ ``SKIP_REASON_LABELS``)."""
        self.skipped[reason] += 1


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Итог прогона: путь, режим и статистика (для CLI и тестов)."""

    output: Path
    dry_run: bool
    stats: GenerationStats

    def summary_lines(self) -> list[str]:
        """Строки итоговой сводки для stdout."""
        stats = self.stats
        lines = [
            f"Датасет LoRA: schema_version={SCHEMA_VERSION}",
            f"Кандидатов после фильтров: {stats.candidates}",
            f"Отобрано к сборке: {stats.sampled} (limit={stats.limit})",
            f"Экспортировано записей: {stats.exported}",
            f"Пропущено записей: {stats.skipped_total}",
        ]
        for reason, count in sorted(stats.skipped.items()):
            label = SKIP_REASON_LABELS.get(reason, reason)
            lines.append(f"  {label}: {count}")
        if stats.levels:
            distribution = ", ".join(
                f"{level_name}={count}"
                for level_name, count in sorted(stats.levels.items())
            )
        else:
            distribution = "нет данных"
        lines.append(f"Распределение по уровням: {distribution}")
        if self.dry_run:
            lines.append("Режим: --dry-run (файл не записывался)")
        else:
            lines.append(f"Файл: {self.output}")
        if stats.exported < stats.limit and stats.skipped_total:
            lines.append(
                "Записей меньше --limit: часть кандидатов пропущена "
                "(см. причины выше) или в БД не хватило правок."
            )
        return lines


# --- чистые функции ---------------------------------------------------------


def count_words(text: str) -> int:
    """Число слов в тексте (``str.split`` — достаточно для ``--min-words``)."""
    return len(text.split())


def filter_candidates(
    candidates: Sequence[Candidate],
    *,
    level: str | None = None,
    min_words: int = 0,
) -> list[Candidate]:
    """Отфильтровать кандидатов по уровню и минимальной длине правки.

    Фильтр по уровню дублируется в SQL (для индексов), но оставлен и здесь:
    функция проверяется в unit-тестах без БД и защищает от расхождений.
    """
    filtered: list[Candidate] = []
    for edit, commentary, game in candidates:
        if level is not None and level_value(commentary.level) != level:
            continue
        if min_words > 0 and count_words(edit.edited_text) < min_words:
            continue
        filtered.append((edit, commentary, game))
    return filtered


def sample_records[T](
    records: Sequence[T],
    *,
    limit: int,
    seed: int | None = None,
) -> list[T]:
    """Детерминированно выбрать не более ``limit`` записей.

    Без ``seed`` возвращает первые ``limit`` элементов (порядок «свежие →
    старые» сохраняется). С ``seed`` перемешивает ``random.Random(seed)``,
    но результат снова сортирует по исходным индексам: выборка
    воспроизводима и остаётся в стабильном порядке.

    Raises:
        ValueError: ``limit < 1``.
    """
    if limit < 1:
        raise ValueError("limit должен быть ≥ 1")
    if limit >= len(records):
        return list(records)
    if seed is None:
        return list(records[:limit])
    rng = random.Random(seed)
    indices = sorted(rng.sample(range(len(records)), limit))
    return [records[index] for index in indices]


def build_record(
    edit: Edit,
    commentary: Commentary,
    game: Game | None,
    analyses: Sequence[MoveAnalysis],
    *,
    min_words: int = 0,
    parsed: ParsedGame | None = None,
) -> dict[str, Any]:
    """Собрать одну v2-запись из уже загруженных строк/анализа.

    Чистая функция (без БД): ``parsed`` можно передать, если PGN уже
    разобран для ``load_analyses``. Форма записи полностью делегирована
    ``record_from_edit`` из ``scripts.export_dataset``.

    Raises:
        RecordSkippedError: ``no_game``, ``min_words``, ``invalid_pgn``,
            ``no_analysis`` или ``invalid_level``.
    """
    if game is None:
        raise RecordSkippedError("no_game")
    if min_words > 0 and count_words(edit.edited_text) < min_words:
        raise RecordSkippedError("min_words")
    if parsed is None:
        try:
            parsed = parse_pgn(game.pgn)
        except InvalidPgnError as exc:
            raise RecordSkippedError("invalid_pgn") from exc
    if not analyses:
        raise RecordSkippedError("no_analysis")
    level = level_value(commentary.level)
    try:
        fact_sheet = export_dataset.build_fact_sheet(parsed, analyses, level)
    except InvalidLevelError as exc:
        raise RecordSkippedError("invalid_level") from exc
    return export_dataset.record_from_edit(
        edit,
        commentary,
        schema_version=SCHEMA_VERSION,
        fact_package=fact_sheet,
    )


async def build_record_from_db(
    session: AsyncSession,
    edit: Edit,
    commentary: Commentary,
    game: Game | None,
    *,
    min_words: int = 0,
) -> dict[str, Any]:
    """Собрать запись, дочитав PGN и анализ ходов из БД.

    Raises:
        RecordSkippedError: причина пропуска (см. ``build_record``).
    """
    if game is None:
        raise RecordSkippedError("no_game")
    try:
        parsed = parse_pgn(game.pgn)
    except InvalidPgnError as exc:
        raise RecordSkippedError("invalid_pgn") from exc
    analyses = await load_analyses(session, game, parsed)
    return build_record(
        edit,
        commentary,
        game,
        analyses,
        min_words=min_words,
        parsed=parsed,
    )


def write_records(output: Path, records: Sequence[dict[str, Any]]) -> int:
    """Записать записи по одной JSON-строке (UTF-8, без ASCII-экранирования)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


# --- выборка из БД ----------------------------------------------------------


def _fetch_limit(limit: int) -> int:
    """Сколько строк читать из БД под целевой ``limit`` (с запасом)."""
    return min(max(limit * CANDIDATE_FETCH_FACTOR, limit), MAX_CANDIDATE_ROWS)


async def load_candidates(
    session: AsyncSession,
    *,
    limit: int,
    level: str | None,
    min_words: int,
) -> list[Candidate]:
    """Прочитать кандидатов: правки с комментариями и партиями.

    Inner join ``edits`` → ``commentaries`` повторяет логику
    ``scripts/export_dataset.py``: экспортируются только отредактированные
    комментарии (у них есть ``edited_text`` — будущий ``assistant``).
    ``games`` — outer join: запись без партии попадёт в счётчик ``no_game``,
    а не потеряется молча.
    """
    statement = (
        select(Edit, Commentary, Game)
        .join(Commentary, Edit.commentary_id == Commentary.id)
        .outerjoin(Game, Commentary.game_id == Game.id)
        .order_by(Edit.created_at.desc(), Edit.id.desc())
        .limit(_fetch_limit(limit))
    )
    if level is not None:
        statement = statement.where(Commentary.level == Level(level))
    rows = (await session.execute(statement)).all()
    candidates: list[Candidate] = [
        (edit, commentary, game) for edit, commentary, game in rows
    ]
    return filter_candidates(candidates, level=level, min_words=min_words)


# --- прогон -----------------------------------------------------------------


def _validate_config(config: DatasetConfig) -> None:
    """Проверить конфиг до обращения к БД (для CLI — exit 2)."""
    if config.limit < 1:
        raise ValueError("--limit должен быть ≥ 1")
    if config.min_words < 0:
        raise ValueError("--min-words не может быть отрицательным")
    if config.level is not None and config.level not in LEVELS:
        raise ValueError(
            f"неизвестный уровень {config.level!r}; "
            f"ожидается {', '.join(LEVELS)} или all"
        )


async def run_export(
    config: DatasetConfig,
    *,
    session_factory: SessionFactory | None = None,
    record_builder: RecordBuilder | None = None,
) -> GenerationResult:
    """Собрать датасет по ``config`` и вернуть статистику.

    Args:
        config: параметры выборки/записи.
        session_factory: фабрика ``AsyncSession``; по умолчанию
            ``app.db.session.SessionLocal``.
        record_builder: сборщик записи; по умолчанию ``build_record_from_db``.
            В тестах подменяется, чтобы не ходить в БД за анализом.
    """
    _validate_config(config)
    factory: SessionFactory = session_factory or SessionLocal
    builder: RecordBuilder = record_builder or build_record_from_db
    stats = GenerationStats(limit=config.limit)
    records: list[dict[str, Any]] = []

    async with factory() as session:
        candidates = await load_candidates(
            session,
            limit=config.limit,
            level=config.level,
            min_words=config.min_words,
        )
        stats.candidates = len(candidates)
        sampled = sample_records(candidates, limit=config.limit, seed=config.seed)
        stats.sampled = len(sampled)
        for edit, commentary, game in sampled:
            if len(records) >= config.limit:
                break
            try:
                record = await builder(
                    session,
                    edit,
                    commentary,
                    game,
                    min_words=config.min_words,
                )
            except RecordSkippedError as exc:
                stats.record_skipped(exc.reason)
                continue
            records.append(record)
            stats.record_exported(level_value(commentary.level))

    # Пустой датасет не создаёт/не перезаписывает файл: exit 2 сообщит об
    # ошибке, а старый датасет не должен выглядеть «свежим».
    if records and not config.dry_run:
        write_records(config.output, records)
    return GenerationResult(output=config.output, dry_run=config.dry_run, stats=stats)


# --- CLI --------------------------------------------------------------------


def _positive_int(value: str) -> int:
    """argparse-тип для ``--limit``: целое ≥ 1."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if number < 1:
        raise argparse.ArgumentTypeError("должно быть ≥ 1")
    return number


def _non_negative_int(value: str) -> int:
    """argparse-тип для ``--min-words``: целое ≥ 0."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if number < 0:
        raise argparse.ArgumentTypeError("должно быть ≥ 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер сборщика датасета."""
    parser = argparse.ArgumentParser(
        prog="generate_lora_dataset_from_db.py",
        description=(
            "Собрать v2 JSONL для LoRA из edits/commentaries/games "
            "(schema_version=2, совместим с scripts/train_lora.py)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help="сколько записей собрать (по умолчанию 500)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=(
            "куда писать JSONL "
            f"(по умолчанию {DEFAULT_OUTPUT})"
        ),
    )
    parser.add_argument(
        "--level",
        choices=(*LEVELS, "all"),
        default="all",
        help="уровень комментариев: beginner | club | gm | all",
    )
    parser.add_argument(
        "--min-words",
        type=_non_negative_int,
        default=0,
        metavar="N",
        help="минимальная длина правки в словах (0 — без фильтра)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="посчитать статистику и пропуски, но не писать файл",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="seed детерминированной выборки (без него — свежие записи)",
    )
    return parser


def _no_data_message(config: DatasetConfig) -> str:
    """Понятное сообщение для exit code 2 при пустой выборке."""
    level = config.level or "all"
    return (
        "Ошибка: не найдено партий с комментариями для экспорта "
        f"(level={level}, limit={config.limit}).\n"
        "Проверьте, что в БД есть edits, а для партий выполнен /v1/analyze:\n"
        "  python scripts/generate_lora_dataset_from_db.py --dry-run"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    session_factory: SessionFactory | None = None,
    record_builder: RecordBuilder | None = None,
) -> int:
    """Точка входа CLI: вернуть exit code (argparse сам даёт 2 на ошибках).

    ``session_factory``/``record_builder`` — точки внедрения для тестов:
    позволяют прогнать CLI без PostgreSQL.
    """
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = DatasetConfig(
        output=args.output,
        limit=args.limit,
        level=None if args.level == "all" else args.level,
        min_words=args.min_words,
        seed=args.seed,
        dry_run=args.dry_run,
    )
    try:
        result = asyncio.run(
            run_export(
                config,
                session_factory=session_factory,
                record_builder=record_builder,
            )
        )
    except Exception as exc:  # БД/сеть: понятная ошибка вместо traceback
        print(f"Ошибка сборки датасета: {exc}", file=sys.stderr)
        return 2

    for line in result.summary_lines():
        print(line)
    if result.stats.exported == 0:
        print(_no_data_message(config), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
