"""Сборка «золотого» датасета из PGN Lichess с оценками Stockfish (T-092).

PNG из ``data/gold_pgns/`` содержат комментарии вида
``{ [%eval 0.23] }``. Скрипт:

1. парсит партию и достаёт оценки ``[%eval ...]`` (White POV);
2. строит :class:`~app.engine.models.MoveAnalysis` без выдуманных «лучших
   ходов» (``best_move_known=False``);
3. собирает пакет фактов :func:`app.llm.prompts.build_fact_package`;
4. генерирует ответ локальной LLM (или mock-клиентом) на русском;
5. валидирует ходы через :func:`app.engine.validator.validate_llm_response`;
6. пишет ``schema_version=2`` JSONL в ``data/gold_dataset.jsonl``.

Почему не БД: оценки уже есть в PGN (Lichess computer analysis), поэтому
повторный прогон Stockfish не нужен. Оценки Lichess даны за белых; скрипт
пересчитывает их в «за сторону хода», как этого ждёт ``MoveAnalysis``.

Запуск::

    python scripts/build_gold_dataset.py --provider mock --limit 10
    python scripts/build_gold_dataset.py --provider local --limit 1000
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import re
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chess  # noqa: E402
import chess.pgn  # noqa: E402

from app.dataset.export import load_system_prompt  # noqa: E402
from app.engine.models import MATE_CP_BASE, MoveAnalysis  # noqa: E402
from app.engine.service import EngineService  # noqa: E402
from app.engine.validator import validate_llm_response  # noqa: E402
from app.errors import InvalidPgnError  # noqa: E402
from app.ingestion import ParsedGame, parse_pgn  # noqa: E402
from app.llm.base import BaseLLMClient  # noqa: E402
from app.llm.factory import get_llm_client  # noqa: E402
from app.llm.prompts import build_fact_package  # noqa: E402

__all__ = [
    "DEFAULT_INPUT_DIR",
    "DEFAULT_LIMIT",
    "DEFAULT_OUTPUT",
    "MOCK_RESPONSE",
    "SKIP_REASON_LABELS",
    "EvalPoint",
    "GoldConfig",
    "GoldResult",
    "GoldStats",
    "analyses_from_evals",
    "build_parser",
    "build_record",
    "export_game_pgn",
    "extract_eval_points",
    "iter_games",
    "main",
    "parse_eval_comment",
    "run_build",
    "write_records",
]

#: Каталог с PGN Lichess по умолчанию.
DEFAULT_INPUT_DIR = PROJECT_ROOT / "data" / "gold_pgns"
#: Куда писать золотой датасет.
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "gold_dataset.jsonl"
#: Целевое число записей.
DEFAULT_LIMIT = 1000
#: Ответ mock-клиента: без SAN-ходов, поэтому проходит валидатор.
MOCK_RESPONSE = (
    "Партия прошла в напряжённой борьбе. Белые активно действовали в "
    "центре, а чёрные искали контригру на ферзевом фланге. Оценки движка "
    "показывают, что борьба шла с переменным успехом, а ключевые моменты "
    "связаны с ошибками в миттельшпиле. В целом это типичная клубная партия "
    "с обоюдными шансами."
)

#: Причины пропуска → подпись для сводки.
SKIP_REASON_LABELS: dict[str, str] = {
    "invalid_pgn": "битый PGN",
    "no_evals": "нет [%eval]",
    "invalid_evals": "оценки не совпали с ходами",
    "invalid_llm": "LLM не прошла валидатор",
}

_EVAL_RE = re.compile(r"\[%eval\s+([^\]]+)\]")


@dataclass(frozen=True, slots=True)
class EvalPoint:
    """Оценка после полухода: cp за белых или мат в N."""

    cp: int | None
    mate: int | None = None


def parse_eval_comment(comment: str) -> EvalPoint | None:
    """Извлечь ``[%eval …]`` из комментария; ``None`` — оценки нет.

    Lichess отдаёт оценку **за белых**: ``0.23`` — +23 cp, ``#-3`` — мат
    чёрных в 3. Мат кодируется как ``±(MATE_CP_BASE − 100·N)``.
    """
    match = _EVAL_RE.search(comment)
    if match is None:
        return None
    raw = match.group(1).strip()
    if raw.startswith("#"):
        try:
            mate = int(raw[1:])
        except ValueError:
            return None
        if mate == 0:
            return None
        sign = -1 if mate < 0 else 1
        cp = sign * (MATE_CP_BASE - 100 * abs(mate))
        return EvalPoint(cp=cp, mate=mate)
    try:
        cp = round(float(raw) * 100)
    except ValueError:
        return None
    return EvalPoint(cp=cp)


def extract_eval_points(game: chess.pgn.Game) -> list[EvalPoint | None]:
    """Оценки после каждого хода mainline (по одной на полуход)."""
    return [
        parse_eval_comment(node.comment or "") for node in game.mainline()
    ]


def export_game_pgn(game: chess.pgn.Game) -> str:
    """Сериализовать одну партию в PGN (заголовки + комментарии)."""
    exporter = chess.pgn.StringExporter(
        headers=True, variations=False, comments=True
    )
    return str(game.accept(exporter))


def _uci_for_ply(parsed: ParsedGame, ply: int) -> str:
    """UCI сыгранного хода: SAN из ParsedGame разыгран на позиции до хода."""
    fen_before = (
        parsed.initial_fen if ply <= 1 else parsed.moves[ply - 2].fen
    )
    board = chess.Board(fen_before)
    move = board.parse_san(parsed.moves[ply - 1].san)
    return move.uci()


def analyses_from_evals(
    parsed: ParsedGame,
    eval_points: Sequence[EvalPoint | None],
) -> list[MoveAnalysis]:
    """Собрать ``MoveAnalysis`` из оценок Lichess (без лучшего хода).

    ``eval_before``/``eval_after`` пересчитываются в систему координат
    игрока, сделавшего ход; ``delta_cp = max(0, before − after)``.
    ``best_move_known=False``: в PGN есть только оценка, а не лучший ход.

    Raises:
        ValueError: число оценок не совпало с числом полуходов.
    """
    if len(eval_points) != parsed.ply_count:
        raise ValueError(
            f"оценок {len(eval_points)}, а полуходов {parsed.ply_count}"
        )
    analyses: list[MoveAnalysis] = []
    white_before = 0
    for index, move in enumerate(parsed.moves):
        point = eval_points[index]
        white_after = (
            point.cp
            if point is not None and point.cp is not None
            else white_before
        )
        sign = 1 if move.ply % 2 == 1 else -1
        eval_before = white_before * sign
        eval_after = white_after * sign
        delta_cp = max(0, eval_before - eval_after)
        uci = _uci_for_ply(parsed, move.ply)
        analyses.append(
            MoveAnalysis(
                ply=move.ply,
                played_move_san=move.san,
                played_move_uci=uci,
                eval_before=eval_before,
                eval_after=eval_after,
                best_move_uci=uci,
                best_move_san=move.san,
                best_move_known=False,
                delta_cp=delta_cp,
                classification=EngineService.classify_move(delta_cp),
                mate_after=point.mate if point is not None else None,
                lines=[],
                motifs=[],
            )
        )
        white_before = white_after
    return analyses


def iter_games(path: Path) -> list[chess.pgn.Game]:
    """Прочитать все партии из одного PGN-файла."""
    text = path.read_text(encoding="utf-8")
    stream = io.StringIO(text)
    games: list[chess.pgn.Game] = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        games.append(game)
    return games


@dataclass(frozen=True, slots=True)
class GoldConfig:
    """Параметры сборки золотого датасета."""

    input_dir: Path = DEFAULT_INPUT_DIR
    output: Path = DEFAULT_OUTPUT
    limit: int = DEFAULT_LIMIT
    level: str = "club"
    provider: str = "local"
    max_retries: int = 1


@dataclass
class GoldStats:
    """Счётчики сборки: файлы, партии, записи, причины пропусков."""

    files: int = 0
    games: int = 0
    converted: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    assistant_chars: int = 0
    eval_points: int = 0

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())

    @property
    def average_assistant_chars(self) -> int:
        return (
            self.assistant_chars // self.converted if self.converted else 0
        )

    def record_skipped(self, reason: str) -> None:
        self.skipped[reason] += 1

    def record_converted(self, chars: int, eval_points: int) -> None:
        self.converted += 1
        self.assistant_chars += chars
        self.eval_points += eval_points


@dataclass(frozen=True, slots=True)
class GoldResult:
    """Итог сборки."""

    output: Path
    stats: GoldStats

    def summary_lines(self) -> list[str]:
        stats = self.stats
        lines = [
            "Золотой датасет: schema_version=2",
            f"PGN-файлов: {stats.files}",
            f"Партий: {stats.games}",
            f"Записей: {stats.converted}",
            f"Пропущено: {stats.skipped_total}",
        ]
        for reason, count in sorted(stats.skipped.items()):
            lines.append(f"  {SKIP_REASON_LABELS.get(reason, reason)}: {count}")
        lines.append(
            "Средняя длина assistant: "
            f"{stats.average_assistant_chars} символов"
        )
        lines.append(f"Файл: {self.output}")
        return lines


def _metadata(
    *,
    source_id: str,
    game: chess.pgn.Game,
    parsed: ParsedGame,
    level: str,
    model: str,
    fact_sheet: str,
    text: str,
    eval_points: int,
) -> dict[str, Any]:
    return {
        "id": source_id,
        "commentary_id": None,
        "game_id": game.headers.get("Site"),
        "editor_id": None,
        "level": level,
        "model": model,
        "created_at": None,
        "fact_sheet_chars": len(fact_sheet),
        "assistant_chars": len(text),
        "eval_points": eval_points,
        "source": "lichess",
        "site": game.headers.get("Site"),
        "white": parsed.metadata.white,
        "black": parsed.metadata.black,
        "white_elo": game.headers.get("WhiteElo"),
        "black_elo": game.headers.get("BlackElo"),
    }


def build_record(
    *,
    game: chess.pgn.Game,
    parsed: ParsedGame,
    fact_sheet: str,
    text: str,
    system_prompt: str,
    level: str,
    model: str,
    source_id: str,
    eval_points: int,
) -> dict[str, Any]:
    """Собрать v2-запись: system → пакет фактов → ответ LLM."""
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": fact_sheet},
            {"role": "assistant", "content": text},
        ],
        "schema_version": 2,
        "metadata": _metadata(
            source_id=source_id,
            game=game,
            parsed=parsed,
            level=level,
            model=model,
            fact_sheet=fact_sheet,
            text=text,
            eval_points=eval_points,
        ),
    }


def _retry_prompt(package: str, illegal: Sequence[str]) -> str:
    return (
        f"{package}\n\nПредыдущий ответ содержал нелегальные ходы: "
        f"{', '.join(illegal)}. Исправь ответ, используй только ходы из "
        "пакета фактов."
    )


async def _generate_text(
    *,
    parsed: ParsedGame,
    package: str,
    system_prompt: str,
    provider: str,
    client: BaseLLMClient | None,
    max_retries: int,
) -> tuple[str, str, bool]:
    """Сгенерировать и провалидировать ответ; вернуть (text, model, ok)."""
    if provider == "mock":
        text = MOCK_RESPONSE
        legal, _ = await validate_llm_response(
            text,
            parsed.initial_fen,
            mainline_san=[move.san for move in parsed.moves],
        )
        return text, "mock", legal
    if client is None:
        raise ValueError("для --provider local нужен LLM-клиент")

    mainline = [move.san for move in parsed.moves]
    prompt = package
    for _ in range(max_retries + 1):
        result = await client.complete(prompt, system_prompt=system_prompt)
        legal, illegal = await validate_llm_response(
            result.text, parsed.initial_fen, mainline_san=mainline
        )
        if legal:
            return result.text, client.model, True
        prompt = _retry_prompt(package, illegal)
    return "", client.model, False


def write_records(
    output: Path, records: Sequence[Mapping[str, Any]]
) -> int:
    """Записать записи по одной JSON-строке (UTF-8)."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


async def run_build(
    config: GoldConfig,
    *,
    llm_client: BaseLLMClient | None = None,
) -> GoldResult:
    """Собрать датасет из PGN-каталога.

    Args:
        config: пути, лимит, уровень, provider.
        llm_client: готовый клиент (тесты); для local без него создаётся
            через `get_llm_client`.

    Raises:
        FileNotFoundError: каталога/PGN нет.
        ValueError: неверный конфиг.
    """
    if config.limit < 1:
        raise ValueError("--limit должен быть ≥ 1")
    if config.provider not in {"local", "mock"}:
        raise ValueError("--provider должен быть local или mock")
    if config.max_retries < 0:
        raise ValueError("--max-retries не может быть отрицательным")
    if not config.input_dir.is_dir():
        raise FileNotFoundError(f"каталог PGN не найден: {config.input_dir}")
    files = sorted(config.input_dir.glob("*.pgn"))
    if not files:
        raise FileNotFoundError(f"в {config.input_dir} нет *.pgn")

    stats = GoldStats(files=len(files))
    records: list[dict[str, Any]] = []
    system_prompt = load_system_prompt(config.level)
    client = llm_client
    owns_client = False
    if config.provider == "local" and client is None:
        client = get_llm_client(config.level)
        owns_client = True
    if client is not None:
        client.set_level(config.level)

    try:
        for path in files:
            file_games = iter_games(path)
            if not file_games:
                stats.record_skipped("invalid_pgn")
                continue
            for game_index, game in enumerate(file_games, start=1):
                if len(records) >= config.limit:
                    break
                stats.games += 1
                pgn = export_game_pgn(game)
                try:
                    parsed = parse_pgn(pgn)
                except InvalidPgnError:
                    stats.record_skipped("invalid_pgn")
                    continue
                points = extract_eval_points(game)
                if len(points) != parsed.ply_count:
                    stats.record_skipped("invalid_evals")
                    continue
                if not any(point is not None for point in points):
                    stats.record_skipped("no_evals")
                    continue
                try:
                    analyses = analyses_from_evals(parsed, points)
                except ValueError:
                    stats.record_skipped("invalid_evals")
                    continue
                fact_sheet = build_fact_package(
                    parsed, analyses, config.level
                )
                text, model, ok = await _generate_text(
                    parsed=parsed,
                    package=fact_sheet,
                    system_prompt=system_prompt,
                    provider=config.provider,
                    client=client,
                    max_retries=config.max_retries,
                )
                if not ok:
                    stats.record_skipped("invalid_llm")
                    continue
                source_id = f"{path.name}:{game_index}"
                records.append(
                    build_record(
                        game=game,
                        parsed=parsed,
                        fact_sheet=fact_sheet,
                        text=text,
                        system_prompt=system_prompt,
                        level=config.level,
                        model=model,
                        source_id=source_id,
                        eval_points=sum(
                            point is not None for point in points
                        ),
                    )
                )
                stats.record_converted(
                    len(text), sum(point is not None for point in points)
                )
            if len(records) >= config.limit:
                break
    finally:
        if owns_client and client is not None:
            await client.aclose()

    if records:
        write_records(config.output, records)
    return GoldResult(output=config.output, stats=stats)


def _positive_int(value: str) -> int:
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
    """CLI-парсер сборщика золотого датасета."""
    parser = argparse.ArgumentParser(
        prog="build_gold_dataset.py",
        description=(
            "Собрать schema_version=2 из PGN Lichess с [%eval], "
            "сгенерировать ответ LLM и провалидировать ходы."
        ),
    )
    parser.add_argument(
        "--input-dir", type=Path, default=DEFAULT_INPUT_DIR
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--limit", type=_positive_int, default=DEFAULT_LIMIT
    )
    parser.add_argument(
        "--level",
        choices=("beginner", "club", "gm"),
        default="club",
    )
    parser.add_argument(
        "--provider", choices=("local", "mock"), default="local"
    )
    parser.add_argument(
        "--max-retries", type=_non_negative_int, default=1
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Точка входа CLI: 0 — есть записи, 2 — ошибка/пусто."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    config = GoldConfig(
        input_dir=args.input_dir,
        output=args.output,
        limit=args.limit,
        level=args.level,
        provider=args.provider,
        max_retries=args.max_retries,
    )
    try:
        result = asyncio.run(run_build(config))
    except (OSError, ValueError) as exc:
        print(f"Ошибка сборки золотого датасета: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # LLM/HTTP: понятная ошибка вместо traceback
        print(f"Ошибка генерации: {exc}", file=sys.stderr)
        return 2

    for line in result.summary_lines():
        print(line)
    if result.stats.converted == 0:
        print(
            "Ни одной записи не собрано: проверьте PGN и наличие [%eval].",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
