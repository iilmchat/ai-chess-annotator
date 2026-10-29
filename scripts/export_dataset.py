"""Экспорт `edits` в JSONL для LoRA-дообучения (T-104/T-034, KI-039).

Поддерживаются две схемы строки (флаг ``--schema-version``):

* **v2 (default, KI-039)** — модель учится писать комментарий *из пакета
  фактов*, а не переписывать готовый текст:

  ```json
  {"messages": [{"role": "system", "content": "Ты — шахматный комментатор..."},
                {"role": "user", "content": "# Партия\\nБелые: ..."},
                {"role": "assistant", "content": "правка редактора"}],
   "schema_version": 2,
   "metadata": {"id": 1, "commentary_id": 2, "game_id": 3, "editor_id": null,
                "level": "club", "model": "qwen", "created_at": "...",
                "fact_sheet_chars": 1234}}
  ```

  `user` — сериализованный пакет фактов:
  `game.pgn → parse_pgn → load_analyses(session, game, parsed) →
  build_fact_package(parsed, analyses, level)`. Записи без доступной партии
  или анализа ходов пропускаются с предупреждением и счётчиком (скрипт не
  падает).

* **v1 (legacy)** — пара «было → стало»: `user` = `edits.original_text`,
  `assistant` = `edits.edited_text`. Сохранена для сравнения и для старых
  выгрузок; модель на ней учится rewrite, а не генерацию из фактов.

`system` в обеих схемах берётся из `app/prompts/<level>.txt`, поэтому
датасет совместим с маскированием лосса в `scripts/train_lora.py`.

v2 экспортирует пакет фактов пакетами (keyset-пагинация по `edits.id`), а не
через один стриминговый курсор: внутри обработки записи нужны запросы
`load_analyses`, а asyncpg не допускает параллельные операции на одном
соединении.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.commentary.service import load_analyses  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Commentary, Edit, Game, Level  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.errors import InvalidLevelError, InvalidPgnError  # noqa: E402
from app.ingestion import parse_pgn  # noqa: E402

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_OUTPUT",
    "LEGACY_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "SKIP_REASON_LABELS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "ExportStats",
    "build_fact_sheet",
    "load_system_prompt",
    "main",
    "record_from_edit",
]

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_dataset.jsonl"
# T-034b/KI-039: переиспользуемые builder'ы вынесены в app.dataset.export,
# чтобы API-слой (app.api.v1.admin_exports) не импортировал scripts/
# (production wheel содержит только app). Реэкспорт сохраняет публичный
# контракт скрипта: тесты и train_lora импортируют имена отсюда.
from app.dataset.export import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SKIP_REASON_LABELS,
    SUPPORTED_SCHEMA_VERSIONS,
    ExportStats,
    build_fact_sheet,
    load_system_prompt,
    record_from_edit,
)
from app.dataset.export import (  # noqa: E402
    level_value as _level_value,
)


def _warn(message: str) -> None:
    """Предупреждение в stderr (stdout — машиночитаемая статистика)."""
    print(f"ПРЕДУПРЕЖДЕНИЕ: {message}", file=sys.stderr)


def _write_record(stream: Any, record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def _export_legacy(
    session: AsyncSession,
    output: Path,
    *,
    limit: int | None,
    level: str | None,
    min_id: int,
) -> int:
    """Экспорт v1: пара `original_text → edited_text` (rewrite)."""
    statement = (
        select(Edit, Commentary)
        .join(Commentary, Edit.commentary_id == Commentary.id)
        .where(Edit.id >= min_id)
        .order_by(Edit.id)
    )
    if level:
        statement = statement.where(Commentary.level == Level(level))
    if limit is not None:
        statement = statement.limit(limit)

    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    result = await session.stream(statement)
    with output.open("w", encoding="utf-8") as stream:
        async for edit, commentary in result:
            _write_record(
                stream,
                record_from_edit(
                    edit, commentary, schema_version=LEGACY_SCHEMA_VERSION
                ),
            )
            count += 1
    return count


async def _export_v2(
    session: AsyncSession,
    output: Path,
    *,
    limit: int | None,
    level: str | None,
    min_id: int,
    stats: ExportStats,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    """Экспорт v2: `system → факт-пакет → правка редактора`.

    Читает `edits` keyset-пагинацией (без загрузки всей таблицы в память),
    для каждой записи восстанавливает партию/анализ и пакет фактов.
    Проблемные записи (нет партии, битый PGN, нет анализа) пропускаются —
    скрипт продолжает работу и печатает статистику.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    last_id = min_id - 1
    remaining = limit
    with output.open("w", encoding="utf-8") as stream:
        while remaining is None or remaining > 0:
            current_batch = (
                batch_size
                if remaining is None
                else min(batch_size, remaining)
            )
            statement = (
                select(Edit, Commentary, Game)
                .join(Commentary, Edit.commentary_id == Commentary.id)
                .outerjoin(Game, Commentary.game_id == Game.id)
                .where(Edit.id > last_id)
                .order_by(Edit.id)
                .limit(current_batch)
            )
            if level:
                statement = statement.where(Commentary.level == Level(level))
            rows = (await session.execute(statement)).all()
            if not rows:
                break
            for edit, commentary, game in rows:
                last_id = edit.id
                if remaining is not None:
                    remaining -= 1
                record = await _record_v2_or_skip(
                    session, edit, commentary, game, stats
                )
                if record is not None:
                    _write_record(stream, record)


async def _record_v2_or_skip(
    session: AsyncSession,
    edit: Edit,
    commentary: Commentary,
    game: Game | None,
    stats: ExportStats,
) -> dict[str, Any] | None:
    """Собрать v2-запись или учесть пропуск; вернуть `None` при пропуске."""
    level = _level_value(commentary.level)
    if game is None:
        _warn(
            f"edit {edit.id} пропущен: нет партии для комментария "
            f"{commentary.id}"
        )
        stats.record_skipped("no_game")
        return None
    try:
        parsed = parse_pgn(game.pgn)
    except InvalidPgnError as exc:
        _warn(
            f"edit {edit.id} пропущен: PGN партии {game.id} не разобран "
            f"({exc})"
        )
        stats.record_skipped("invalid_pgn")
        return None
    analyses = await load_analyses(session, game, parsed)
    if not analyses:
        _warn(
            f"edit {edit.id} пропущен: у партии {game.id} нет анализа ходов "
            "(сначала POST /v1/analyze)"
        )
        stats.record_skipped("no_analysis")
        return None
    try:
        fact_sheet = build_fact_sheet(parsed, analyses, level)
    except InvalidLevelError as exc:
        _warn(f"edit {edit.id} пропущен: {exc}")
        stats.record_skipped("invalid_level")
        return None
    stats.record_exported(level, fact_sheet)
    return record_from_edit(
        edit,
        commentary,
        schema_version=SCHEMA_VERSION,
        fact_package=fact_sheet,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Экспорт edits в JSONL для LoRA (T-104): system→user→assistant. "
            "v2 — user = пакет фактов; v1 — user = original_text (legacy)."
        )
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "сколько строк edits просмотреть (в v2 считаются и пропущенные "
            "записи: no_game/invalid_pgn/no_analysis)"
        ),
    )
    parser.add_argument(
        "--level", choices=["beginner", "club", "gm"], default=None
    )
    parser.add_argument("--min-id", type=int, default=0)
    parser.add_argument(
        "--schema-version",
        type=int,
        choices=SUPPORTED_SCHEMA_VERSIONS,
        default=SCHEMA_VERSION,
        help=(
            "версия схемы JSONL: 2 — user=пакет фактов (default), "
            "1 — legacy original_text→edited_text"
        ),
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    exported = 0
    async with SessionLocal() as session:
        if args.schema_version == LEGACY_SCHEMA_VERSION:
            exported = await _export_legacy(
                session,
                args.output,
                limit=args.limit,
                level=args.level,
                min_id=args.min_id,
            )
            print(f"Экспортировано записей: {exported} (schema_version=1)")
        else:
            stats = ExportStats(schema_version=SCHEMA_VERSION)
            await _export_v2(
                session,
                args.output,
                limit=args.limit,
                level=args.level,
                min_id=args.min_id,
                stats=stats,
            )
            for line in stats.summary_lines():
                print(line)
            exported = stats.exported
    print(f"Файл: {args.output}")
    if args.schema_version == SCHEMA_VERSION and exported == 0:
        print(
            "Ни одной v2-записи не экспортировано. Проверьте, что для "
            "отредактированных партий выполнен /v1/analyze и есть moves.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.limit is not None and args.limit < 1:
        print("--limit должен быть ≥ 1", file=sys.stderr)
        return 2
    get_settings()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
