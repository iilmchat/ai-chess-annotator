"""Конвертация внешних PGN-датасетов в v2 JSONL для LoRA (T-087).

Скрипт читает JSONL, где поле ``text`` содержит PGN с комментариями в
фигурных скобках, и собирает датасет ``schema_version=2`` для
``scripts/train_lora.py``:

* ``system`` — системный промпт уровня ``club`` (``app/prompts/club.txt``);
* ``user`` — пакет фактов ``build_fact_package(parsed, level="club")``;
* ``assistant`` — склеенные текстовые комментарии из PGN.

Комментарии очищаются от PGN-тегов ``[%eval ...]``/``[%clk ...]`` и
ограничиваются ``MAX_ASSISTANT_CHARS`` символами. Внешний PGN не содержит
анализа движка, поэтому пакет фактов собирается без ключевых моментов:
``build_fact_package`` явно помечает, что оценок движка в нём нет.

Пропуски не прерывают прогон: битый JSON/PGN, пустой ``text``, отсутствие
комментариев и слишком короткие комментарии учитываются в сводке.
Один и тот же PGN может встречаться в датасете несколько раз — дедупликация
не выполняется, чтобы не терять разные аннотации.

Запуск::

    python scripts/convert_external_datasets.py --dry-run --limit 100
    python scripts/convert_external_datasets.py \\
        --limit 5000 --output data/lora_gold_v1.jsonl
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chess.pgn  # noqa: E402

from app.dataset.export import load_system_prompt  # noqa: E402
from app.errors import InvalidPgnError  # noqa: E402
from app.ingestion import parse_pgn  # noqa: E402
from app.llm.prompts import build_fact_package  # noqa: E402

__all__ = [
    "DEFAULT_INPUT",
    "DEFAULT_LIMIT",
    "DEFAULT_OUTPUT",
    "MAX_ASSISTANT_CHARS",
    "MIN_COMMENT_CHARS",
    "SCHEMA_VERSION",
    "SKIP_REASON_LABELS",
    "SOURCE_NAME",
    "Conversion",
    "ConversionConfig",
    "ConversionResult",
    "ConversionStats",
    "build_parser",
    "clean_comment",
    "convert_record",
    "extract_comments",
    "extract_pgn",
    "main",
    "merge_comments",
    "run_conversion",
    "truncate_text",
    "write_records",
]

#: Актуальная версия схемы JSONL (см. ``app/dataset/export.py``).
SCHEMA_VERSION = 2
#: Входной датасет по умолчанию (скачивается вручную, в git не попадает).
DEFAULT_INPUT = (
    PROJECT_ROOT / "data" / "external_datasets" / "bishop_train.jsonl"
)
#: Куда писать gold-датасет по умолчанию.
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "lora_gold_v1.jsonl"
#: Сколько записей максимум писать в выходной файл.
DEFAULT_LIMIT = 5000
#: Уровень, под который собирается пакет фактов и system-промпт.
DEFAULT_LEVEL = "club"
#: Комментарий короче этого порога бесполезен как обучающий ответ.
MIN_COMMENT_CHARS = 20
#: Верхняя граница ``assistant`` (символов): длинные склейки обрезаются.
MAX_ASSISTANT_CHARS = 2000
#: Имя источника в ``metadata``.
SOURCE_NAME = "pkloats/bishop-chess-dataset"

#: PGN-теги вида ``[%eval 0.35]``, ``[%clk 0:03:00]`` — служебные, не текст.
_TAG_RE = re.compile(r"\[%[^\]]*\]", flags=re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

#: Причины пропуска → человекочитаемая подпись для сводки.
SKIP_REASON_LABELS: dict[str, str] = {
    "invalid_json": "битый JSON",
    "empty_text": "пустое или отсутствующее поле text",
    "invalid_pgn": "битый PGN",
    "no_comments": "нет комментариев",
    "short_comments": "комментарии короче MIN_COMMENT_CHARS символов",
}


@contextmanager
def _quiet_chess_log() -> Iterator[None]:
    """Не засорять stderr ошибками python-chess на каждой битой партии."""
    logger = logging.getLogger("chess.pgn")
    previous_level = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous_level)


def clean_comment(text: str) -> str:
    """Убрать PGN-теги (``[%eval``/``[%clk``/…) и лишние пробелы.

    Args:
        text: сырой комментарий из ``node.comment``.

    Returns:
        Чистый текст без служебных тегов, с одиночными пробелами.
    """
    without_tags = _TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", without_tags).strip()


def truncate_text(
    text: str, *, limit: int = MAX_ASSISTANT_CHARS
) -> str:
    """Обрезать текст до ``limit`` символов по границе слова.

    Raises:
        ValueError: ``limit < 1``.
    """
    if limit < 1:
        raise ValueError("limit должен быть ≥ 1")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip()
    # Одно очень длинное слово: режем ровно по лимиту, иначе получим пустоту.
    return cut or text[:limit]


def extract_comments(game: chess.pgn.Game) -> list[str]:
    """Все очищенные комментарии mainline (включая комментарий к партии)."""
    raw: list[str] = []
    if game.comment:
        raw.append(game.comment)
    for node in game.mainline():
        if node.comment:
            raw.append(node.comment)
    cleaned = [clean_comment(comment) for comment in raw]
    return [comment for comment in cleaned if comment]


def merge_comments(comments: Sequence[str]) -> str:
    """Склеить комментарии в один связный текст через пробел."""
    return _WHITESPACE_RE.sub(" ", " ".join(comments)).strip()


def extract_pgn(raw: Mapping[str, Any]) -> str | None:
    """Достать PGN из поля ``text``; ``None`` — поля нет или оно пустое."""
    text = raw.get("text")
    if not isinstance(text, str):
        return None
    return text.strip() or None


@dataclass(frozen=True, slots=True)
class Conversion:
    """Результат конвертации одной строки: запись либо причина пропуска."""

    record: dict[str, Any] | None = None
    skip_reason: str | None = None
    assistant_chars: int = 0


@dataclass(frozen=True, slots=True)
class ConversionConfig:
    """Параметры одного прогона конвертации."""

    input_path: Path = DEFAULT_INPUT
    output_path: Path = DEFAULT_OUTPUT
    limit: int = DEFAULT_LIMIT
    dry_run: bool = False


@dataclass
class ConversionStats:
    """Счётчики прогона: прочитано, сконвертировано, пропуски, длины."""

    limit: int = DEFAULT_LIMIT
    total: int = 0
    converted: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    assistant_chars: int = 0
    assistant_max: int = 0

    @property
    def skipped_total(self) -> int:
        """Всего пропущенных записей."""
        return sum(self.skipped.values())

    @property
    def average_assistant_chars(self) -> int:
        """Средняя длина ``assistant`` (0, если записей нет)."""
        return self.assistant_chars // self.converted if self.converted else 0

    def record_read(self) -> None:
        """Учесть прочитанную непустую строку JSONL."""
        self.total += 1

    def record_converted(self, assistant_chars: int) -> None:
        """Учесть успешно сконвертированную запись."""
        self.converted += 1
        self.assistant_chars += assistant_chars
        self.assistant_max = max(self.assistant_max, assistant_chars)

    def record_skipped(self, reason: str) -> None:
        """Учесть пропуск (причина — ключ ``SKIP_REASON_LABELS``)."""
        self.skipped[reason] += 1


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Итог прогона: конфиг и статистика (для CLI и тестов)."""

    config: ConversionConfig
    stats: ConversionStats

    def summary_lines(self) -> list[str]:
        """Строки итоговой сводки для stdout."""
        stats = self.stats
        lines = [
            f"Конвертация внешнего датасета: schema_version={SCHEMA_VERSION}",
            f"Прочитано записей: {stats.total}",
            f"Сконвертировано записей: {stats.converted}",
            f"Пропущено записей: {stats.skipped_total}",
        ]
        for reason, count in sorted(stats.skipped.items()):
            label = SKIP_REASON_LABELS.get(reason, reason)
            lines.append(f"  {label}: {count}")
        lines.append(
            f"Средняя длина assistant: {stats.average_assistant_chars} "
            f"символов (максимум {stats.assistant_max})"
        )
        if self.config.dry_run:
            lines.append("Режим: --dry-run (файл не записывался)")
        else:
            lines.append(f"Файл: {self.config.output_path}")
        if stats.converted < stats.limit and stats.skipped_total:
            lines.append(
                "Записей меньше --limit: часть кандидатов пропущена "
                "(см. причины выше)."
            )
        return lines


def _source_value(raw: Mapping[str, Any], key: str) -> str | None:
    """Строковое поле источника (``id``/``origin``) или ``None``."""
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _build_metadata(
    raw: Mapping[str, Any],
    *,
    line_number: int,
    level: str,
    source: str,
    fact_sheet: str,
    assistant: str,
    comment_count: int,
) -> dict[str, Any]:
    """Метаданные v2-записи для внешнего датасета (без БД-идентификаторов)."""
    return {
        "id": line_number,
        "commentary_id": None,
        "game_id": None,
        "editor_id": None,
        "level": level,
        "model": source,
        "created_at": None,
        "fact_sheet_chars": len(fact_sheet),
        "assistant_chars": len(assistant),
        "comment_count": comment_count,
        "source": source,
        "source_id": _source_value(raw, "id"),
        "origin": _source_value(raw, "origin"),
    }


def convert_record(
    raw: Mapping[str, Any],
    *,
    line_number: int,
    level: str = DEFAULT_LEVEL,
    min_comment_chars: int = MIN_COMMENT_CHARS,
    max_assistant_chars: int = MAX_ASSISTANT_CHARS,
    source: str = SOURCE_NAME,
) -> Conversion:
    """Сконвертировать одну JSONL-запись в v2-запись или пропуск.

    Args:
        raw: распарсенная строка JSONL (ожидается поле ``text`` с PGN).
        line_number: номер строки во входном файле (для ``metadata.id``).
        level: уровень пакета фактов и system-промпта.
        min_comment_chars: минимальная длина одного комментария.
        max_assistant_chars: максимум символов в поле ``assistant``.
        source: имя источника для ``metadata``.

    Returns:
        ``Conversion`` с записью либо с причиной пропуска.
    """
    pgn_text = extract_pgn(raw)
    if pgn_text is None:
        return Conversion(skip_reason="empty_text")

    with _quiet_chess_log():
        try:
            game = chess.pgn.read_game(io.StringIO(pgn_text))
        except Exception:
            # python-chess бросает ValueError/IndexError на некоторых
            # повреждённых movetext — это штатный «битый PGN», а не сбой.
            return Conversion(skip_reason="invalid_pgn")
        if game is None or game.errors:
            return Conversion(skip_reason="invalid_pgn")
        if not list(game.mainline()):
            return Conversion(skip_reason="invalid_pgn")

        comments = extract_comments(game)
        if not comments:
            return Conversion(skip_reason="no_comments")
        useful = [comment for comment in comments if len(comment) >= min_comment_chars]
        if not useful:
            return Conversion(skip_reason="short_comments")

        assistant = truncate_text(
            merge_comments(useful), limit=max_assistant_chars
        )
        if not assistant:
            return Conversion(skip_reason="no_comments")

        try:
            parsed = parse_pgn(pgn_text)
        except (InvalidPgnError, ValueError, IndexError):
            return Conversion(skip_reason="invalid_pgn")

    fact_sheet = build_fact_package(parsed, level=level)
    system_prompt = load_system_prompt(level)
    record: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": fact_sheet},
            {"role": "assistant", "content": assistant},
        ],
        "schema_version": SCHEMA_VERSION,
        "metadata": _build_metadata(
            raw,
            line_number=line_number,
            level=level,
            source=source,
            fact_sheet=fact_sheet,
            assistant=assistant,
            comment_count=len(useful),
        ),
    }
    return Conversion(record=record, assistant_chars=len(assistant))


def write_records(
    output: Path, records: Sequence[Mapping[str, Any]]
) -> int:
    """Записать записи по одной JSON-строке (UTF-8, без ASCII-экранирования)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def run_conversion(config: ConversionConfig) -> ConversionResult:
    """Прочитать входной JSONL и собрать выходной датасет.

    ``--limit`` ограничивает число записей в выходном файле; чтение идёт до
    тех пор, пока лимит не набран или файл не закончился. Пустой результат
    не перезаписывает существующий выходной файл.

    Raises:
        FileNotFoundError: входного файла нет.
        ValueError: ``limit < 1``.
    """
    if config.limit < 1:
        raise ValueError("--limit должен быть ≥ 1")
    if not config.input_path.is_file():
        raise FileNotFoundError(
            f"входной файл не найден: {config.input_path}"
        )

    stats = ConversionStats(limit=config.limit)
    records: list[dict[str, Any]] = []

    with config.input_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            stats.record_read()
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                stats.record_skipped("invalid_json")
                continue
            if not isinstance(raw, dict):
                stats.record_skipped("invalid_json")
                continue
            conversion = convert_record(raw, line_number=line_number)
            if conversion.record is None:
                stats.record_skipped(conversion.skip_reason or "unknown")
                continue
            records.append(conversion.record)
            stats.record_converted(conversion.assistant_chars)
            if len(records) >= config.limit:
                break

    if records and not config.dry_run:
        write_records(config.output_path, records)
    return ConversionResult(config=config, stats=stats)


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


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер конвертера внешних датасетов."""
    parser = argparse.ArgumentParser(
        prog="convert_external_datasets.py",
        description=(
            "Собрать v2 JSONL для LoRA из внешнего PGN-датасета "
            "(schema_version=2, совместим с scripts/train_lora.py)."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        metavar="PATH",
        help=f"входной JSONL с PGN в поле text (по умолчанию {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        metavar="PATH",
        help=f"куда писать JSONL (по умолчанию {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"максимум записей в выходном файле (по умолчанию {DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="посчитать статистику, но не писать файл",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: вернуть exit code (argparse сам даёт 2 на ошибках)."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = ConversionConfig(
        input_path=args.input,
        output_path=args.output,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    try:
        result = run_conversion(config)
    except (OSError, ValueError) as exc:
        print(f"Ошибка конвертации: {exc}", file=sys.stderr)
        return 2

    for line in result.summary_lines():
        print(line)
    if result.stats.converted == 0:
        print(
            "Ни одной записи не сконвертировано: проверьте поле text и PGN "
            "во входном файле.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
