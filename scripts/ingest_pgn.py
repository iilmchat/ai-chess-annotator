"""CLI: загрузить PGN-файл в базу аннотированных партий (RAG).

Пример:

    python scripts/ingest_pgn.py --file data/sample.pgn --limit 50 \\
        --source my-collection

Скрипт пишет партии в `annotated_games` (эмбеддинг позиции + текст
аннотаций). Это та же операция, что `POST /v1/ingest`, но без HTTP и
rate limiting — удобно для массовой загрузки локальных коллекций.

`--limit` по умолчанию 10: защита от случайной загрузки огромного файла.
Коды выхода: 0 — успех (в том числе `--help`), 1 — в файле нет партий с
ходами, 2 — ошибка аргументов/файла/настроек эмбеддера.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# `python scripts/ingest_pgn.py` кладёт в sys.path только `scripts/`;
# добавляем корень репозитория, чтобы `app` импортировался и без install.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.db.session import SessionLocal, engine  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.rag.service import ingest_pgn_text  # noqa: E402

__all__ = ["DEFAULT_LIMIT", "build_parser", "main"]

#: Сколько партий загружать за запуск, если ``--limit`` не передан.
DEFAULT_LIMIT = 10


def _positive_int(value: str) -> int:
    """argparse-тип для ``--limit``: целое число ≥ 1."""
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"ожидается целое число, получено {value!r}"
        ) from exc
    if number < 1:
        raise argparse.ArgumentTypeError("limit должен быть ≥ 1")
    return number


def _decode(data: bytes) -> str:
    """UTF-8, при неудаче — cp1251 (старые базы PGN)."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    """Собрать парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        description="Загрузить партии из PGN в RAG (annotated_games).",
    )
    parser.add_argument(
        "--file",
        required=True,
        type=Path,
        help="путь к PGN-файлу",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        help=(
            "загрузить только первые N партий "
            f"(по умолчанию {DEFAULT_LIMIT}; должен быть ≥ 1)"
        ),
    )
    parser.add_argument(
        "--source",
        default="upload",
        help="метка источника в annotated_games.source (по умолчанию upload)",
    )
    return parser


async def _run(pgn_text: str, label: str, limit: int | None, source: str) -> int:
    try:
        async with SessionLocal() as session:
            ids = await ingest_pgn_text(
                session, pgn_text, source=source, limit=limit
            )
            if not ids:
                print(
                    f"В файле {label} не найдено партий с ходами",
                    file=sys.stderr,
                )
                return 1
            await session.commit()
    finally:
        # Закрываем пул asyncpg внутри этого event loop (как lifespan).
        await engine.dispose()

    print(f"Файл: {label}")
    print(f"Источник: {source}")
    print(f"Загружено партий: {len(ids)}")
    print(f"ID: {ids[0]}..{ids[-1]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI; возвращает код выхода процесса."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse уже напечатал help/ошибку; возвращаем код, а не выходим,
        # чтобы main() можно было вызывать из тестов.
        return exc.code if isinstance(exc.code, int) else 2

    if not args.file.is_file():
        print(f"Файл не найден: {args.file}", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("--limit должен быть ≥ 1", file=sys.stderr)
        return 2

    # Чтение файла — синхронное, до входа в event loop: это CLI, а не
    # серверный путь, и блокировать нечего.
    try:
        pgn_text = _decode(args.file.read_bytes())
    except OSError as exc:
        print(f"Не удалось прочитать файл {args.file}: {exc}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(_run(pgn_text, str(args.file), args.limit, args.source))
    except (AppError, ValueError) as exc:
        # Фатальная ошибка настройки (например, неизвестный
        # EMBEDDING_PROVIDER) — без трейсбека.
        message = exc.message if isinstance(exc, AppError) else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - интерактивная отмена
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
