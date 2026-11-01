"""Скачивание партий Lichess для «золотого» датасета (T-092).

Скрипт обращается к публичному API Lichess, забирает PGN с оценками
Stockfish (``evals=true``), оставляет партии, где **оба** игрока имеют
рейтинг 1800–2200 (клубный уровень), и раскладывает их по файлам
``data/gold_pgns/*.pgn`` по 50–100 партий.

Два режима:

* ``--username`` — ``GET /api/games/user/{username}`` (последние партии
  игрока, рейтинговые blitz/rapid/classical);
* ``--game-ids`` — ``POST /api/games/export/_ids`` (до 300 ID, тело —
  ID через запятую).

Почему фильтр по рейтингу делается локально: у Lichess API нет параметра
``minRating``/``maxRating`` для экспорта партий. Рейтинги берутся из
заголовков ``WhiteElo``/``BlackElo``; партии без рейтинга или с чужим
вариантом отбрасываются.

Запуск::

    python scripts/fetch_lichess_games.py --username DrNykterstein --max-games 200
    python scripts/fetch_lichess_games.py --game-ids abc12345,def67890
"""

from __future__ import annotations

import argparse
import io
import re
import sys
import urllib.parse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import chess.pgn  # noqa: E402
import httpx  # noqa: E402

__all__ = [
    "DEFAULT_MAX_ELO",
    "DEFAULT_MAX_GAMES",
    "DEFAULT_MIN_ELO",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_PER_FILE",
    "LICHESS_BASE_URL",
    "FetchConfig",
    "FetchError",
    "FetchResult",
    "FetchStats",
    "SelectedGame",
    "build_parser",
    "export_game_pgn",
    "fetch_pgn",
    "filter_games",
    "main",
    "parse_games",
    "run_fetch",
    "write_pgn_chunks",
]

#: Базовый URL Lichess API.
LICHESS_BASE_URL = "https://lichess.org"
#: Куда складывать PGN по умолчанию.
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "gold_pgns"
#: Клубный диапазон рейтинга (включительно).
DEFAULT_MIN_ELO = 1800
DEFAULT_MAX_ELO = 2200
#: Сколько партий запрашивать у API за один раз.
DEFAULT_MAX_GAMES = 200
#: Сколько партий в одном PGN-файле (удобно для ручного просмотра).
DEFAULT_PER_FILE = 50
#: Быстрые контроли, интересные клубному уровню.
DEFAULT_PERF_TYPES = ("blitz", "rapid", "classical")
#: User-Agent обязателен для вежливого обращения к Lichess.
USER_AGENT = "ChessAnnotator/1.0 (gold dataset builder)"
#: Признак оценки Stockfish в PGN-комментарии.
EVAL_MARKER = "[%eval"

_ELO_RE = re.compile(r"^\d+$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")


class FetchError(Exception):
    """Не удалось скачать/разобрать партии Lichess."""


@dataclass(frozen=True, slots=True)
class SelectedGame:
    """Партия, прошедшая фильтр, и её PGN-текст."""

    game: chess.pgn.Game
    pgn: str


@dataclass
class FetchStats:
    """Счётчики скачивания и фильтрации."""

    fetched: int = 0
    selected: int = 0
    skipped_rating: int = 0
    skipped_no_evals: int = 0
    skipped_variant: int = 0
    files: int = 0

    def summary_lines(self, output_dir: Path) -> list[str]:
        """Строки итоговой сводки для stdout."""
        return [
            "Lichess gold PGN:",
            f"  получено партий: {self.fetched}",
            f"  выбрано (клубный рейтинг, evals): {self.selected}",
            f"  пропущено по рейтингу: {self.skipped_rating}",
            f"  пропущено без [%eval]: {self.skipped_no_evals}",
            f"  пропущено не-standard вариант: {self.skipped_variant}",
            f"  файлов записано: {self.files}",
            f"  каталог: {output_dir}",
        ]


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Итог прогона: статистика и созданные файлы."""

    stats: FetchStats
    files: tuple[Path, ...]

    def summary_lines(self, output_dir: Path) -> list[str]:
        return self.stats.summary_lines(output_dir)


@dataclass(frozen=True, slots=True)
class FetchConfig:
    """Параметры одного запуска скачивания."""

    username: str | None = None
    game_ids: tuple[str, ...] = ()
    output_dir: Path = DEFAULT_OUTPUT_DIR
    max_games: int = DEFAULT_MAX_GAMES
    per_file: int = DEFAULT_PER_FILE
    min_elo: int = DEFAULT_MIN_ELO
    max_elo: int = DEFAULT_MAX_ELO


def _validate_config(config: FetchConfig) -> None:
    """Проверить взаимоисключающие параметры и границы."""
    if bool(config.username) == bool(config.game_ids):
        raise ValueError(
            "нужно указать ровно одно: --username или --game-ids"
        )
    if config.max_games < 1:
        raise ValueError("--max-games должен быть ≥ 1")
    if not 1 <= config.per_file <= 100:
        raise ValueError("--per-file должен быть от 1 до 100")
    if config.min_elo < 0 or config.max_elo < config.min_elo:
        raise ValueError("некорректный диапазон --min-elo/--max-elo")
    if len(config.game_ids) > 300:
        raise ValueError("Lichess принимает не более 300 ID за запрос")


def _request_params(max_games: int) -> dict[str, str]:
    """Query-параметры экспорта PGN пользователя с оценками."""
    return {
        "max": str(max_games),
        "rated": "true",
        "perfType": ",".join(DEFAULT_PERF_TYPES),
        "moves": "true",
        "tags": "true",
        "clocks": "false",
        "evals": "true",
        "opening": "false",
        "sort": "dateDesc",
    }


def _ids_request_params() -> dict[str, str]:
    """Query-параметры экспорта по ID (без max/rated/perfType/sort)."""
    return {
        "moves": "true",
        "tags": "true",
        "clocks": "false",
        "evals": "true",
        "opening": "false",
    }


def fetch_pgn(
    client: httpx.Client,
    *,
    username: str | None = None,
    game_ids: Sequence[str] = (),
    max_games: int = DEFAULT_MAX_GAMES,
    base_url: str = LICHESS_BASE_URL,
) -> str:
    """Скачать PGN: по имени пользователя или по списку ID.

    Args:
        client: готовый ``httpx.Client`` (в тестах — с MockTransport).
        username: ник на Lichess.
        game_ids: ID партий (взаимоисключающе с ``username``).
        max_games: сколько последних партий просить у API.
        base_url: базовый URL (для тестов/локального прокси).

    Raises:
        FetchError: не задан ровно один из селекторов или ответ не PGN.
        httpx.HTTPError: сетевые ошибки/не-2xx (пробрасываются).
    """
    if bool(username) == bool(game_ids):
        raise FetchError("нужно указать ровно одно: username или game_ids")

    if username is not None:
        quoted = urllib.parse.quote(username, safe="")
        response = client.get(
            f"{base_url}/api/games/user/{quoted}",
            params=_request_params(max_games),
            headers={"Accept": "application/x-chess-pgn"},
        )
    else:
        response = client.post(
            f"{base_url}/api/games/export/_ids",
            params=_ids_request_params(),
            content=",".join(game_ids),
            headers={
                "Accept": "application/x-chess-pgn",
                "Content-Type": "text/plain",
            },
        )
    response.raise_for_status()
    text = response.text
    if not text.strip():
        raise FetchError("Lichess вернул пустой PGN")
    return text


def parse_games(pgn_text: str) -> list[chess.pgn.Game]:
    """Разобрать поток PGN (один или несколько games) в список партий."""
    stream = io.StringIO(pgn_text)
    games: list[chess.pgn.Game] = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        games.append(game)
    return games


def export_game_pgn(game: chess.pgn.Game) -> str:
    """Сериализовать одну партию обратно в PGN (с комментариями)."""
    exporter = chess.pgn.StringExporter(
        headers=True, variations=False, comments=True
    )
    return str(game.accept(exporter))


def _elo(value: Any) -> int | None:
    """Заголовок Elo → int; пустое/«?»/мусор → None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or not _ELO_RE.match(text):
        return None
    return int(text)


def _is_standard(game: chess.pgn.Game) -> bool:
    """Партия в стандартные шахматы (не 960/atomic/…)."""
    variant = (game.headers.get("Variant") or "Standard").strip().lower()
    return variant in {"standard", ""}


def filter_games(
    games: Sequence[chess.pgn.Game],
    *,
    min_elo: int = DEFAULT_MIN_ELO,
    max_elo: int = DEFAULT_MAX_ELO,
) -> tuple[list[SelectedGame], FetchStats]:
    """Отобрать партии по рейтингу, варианту и наличию ``[%eval]``."""
    stats = FetchStats(fetched=len(games))
    selected: list[SelectedGame] = []
    for game in games:
        if not _is_standard(game):
            stats.skipped_variant += 1
            continue
        white = _elo(game.headers.get("WhiteElo"))
        black = _elo(game.headers.get("BlackElo"))
        if (
            white is None
            or black is None
            or not min_elo <= white <= max_elo
            or not min_elo <= black <= max_elo
        ):
            stats.skipped_rating += 1
            continue
        pgn = export_game_pgn(game)
        if EVAL_MARKER not in pgn:
            stats.skipped_no_evals += 1
            continue
        selected.append(SelectedGame(game=game, pgn=pgn))
    stats.selected = len(selected)
    return selected, stats


def _safe_prefix(value: str) -> str:
    """Безопасное имя файла из ника/идентификатора."""
    cleaned = _SAFE_NAME_RE.sub("_", value).strip("_")
    return cleaned or "lichess"


def write_pgn_chunks(
    games: Sequence[SelectedGame],
    output_dir: Path,
    *,
    prefix: str = "lichess",
    per_file: int = DEFAULT_PER_FILE,
) -> tuple[Path, ...]:
    """Записать партии в ``prefix_001.pgn`` по ``per_file`` штук.

    Raises:
        ValueError: ``per_file`` вне 1…100.
    """
    if not 1 <= per_file <= 100:
        raise ValueError("per_file должен быть от 1 до 100")
    if not games:
        return ()
    output_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_prefix(prefix)
    files: list[Path] = []
    for number, start in enumerate(range(0, len(games), per_file), start=1):
        chunk = games[start : start + per_file]
        path = output_dir / f"{safe}_{number:03d}.pgn"
        body = "\n\n".join(item.pgn.strip() for item in chunk)
        path.write_text(body + "\n", encoding="utf-8")
        files.append(path)
    return tuple(files)


def run_fetch(config: FetchConfig, *, client: httpx.Client) -> FetchResult:
    """Скачать, отфильтровать и разложить партии по файлам."""
    _validate_config(config)
    pgn_text = fetch_pgn(
        client,
        username=config.username,
        game_ids=config.game_ids,
        max_games=config.max_games,
    )
    games = parse_games(pgn_text)
    selected, stats = filter_games(
        games, min_elo=config.min_elo, max_elo=config.max_elo
    )
    prefix = config.username if config.username else "ids"
    files = write_pgn_chunks(
        selected,
        config.output_dir,
        prefix=prefix,
        per_file=config.per_file,
    )
    stats.files = len(files)
    return FetchResult(stats=stats, files=files)


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


def _per_file(value: str) -> int:
    number = _positive_int(value)
    if number > 100:
        raise argparse.ArgumentTypeError("--per-file должен быть ≤ 100")
    return number


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер загрузчика Lichess PGN."""
    parser = argparse.ArgumentParser(
        prog="fetch_lichess_games.py",
        description=(
            "Скачать PGN Lichess с evals=true, отобрать рейтинг 1800–2200 "
            "и сохранить в data/gold_pgns/*.pgn."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--username", help="ник игрока на Lichess")
    source.add_argument(
        "--game-ids",
        help="ID партий через запятую (до 300)",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    parser.add_argument(
        "--max-games", type=_positive_int, default=DEFAULT_MAX_GAMES
    )
    parser.add_argument(
        "--per-file", type=_per_file, default=DEFAULT_PER_FILE
    )
    parser.add_argument(
        "--min-elo", type=int, default=DEFAULT_MIN_ELO
    )
    parser.add_argument(
        "--max-elo", type=int, default=DEFAULT_MAX_ELO
    )
    return parser


def _ids_from_arg(value: str) -> tuple[str, ...]:
    ids = tuple(part.strip() for part in value.split(",") if part.strip())
    if not ids:
        raise ValueError("--game-ids пуст")
    return ids


def main(
    argv: Sequence[str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> int:
    """Точка входа CLI: 0 — есть выбранные партии, 2 — ошибка/пусто."""
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=60.0,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )
    try:
        config = FetchConfig(
            username=args.username,
            game_ids=_ids_from_arg(args.game_ids) if args.game_ids else (),
            output_dir=args.output_dir,
            max_games=args.max_games,
            per_file=args.per_file,
            min_elo=args.min_elo,
            max_elo=args.max_elo,
        )
        result = run_fetch(config, client=http_client)
    except (FetchError, ValueError, OSError, httpx.HTTPError) as exc:
        print(f"Ошибка загрузки Lichess: {exc}", file=sys.stderr)
        return 2
    finally:
        if owns_client:
            http_client.close()

    for line in result.summary_lines(config.output_dir):
        print(line)
    if result.stats.selected == 0:
        print(
            "Ни одной партии не прошло фильтр: проверьте ник/ID, рейтинг "
            "и наличие evals=true.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
