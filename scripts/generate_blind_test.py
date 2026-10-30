"""Генератор «слепого» теста комментариев (T-024, итерация 35).

Задача: взять 3 партии, сгенерировать для каждой комментарии трёх уровней
(``beginner``/``club``/``gm``) и разложить их по файлам так, чтобы человек-
тестировщик не знал, где какой уровень. Три уровня на партию перемешиваются
засеянным ``random.Random(seed)``: слоты ``A``/``B``/``C`` не коррелируют с
уровнем, а один и тот же ``--seed`` даёт одинаковую раскладку (reproducible
blind test прогона).

Что создаётся в ``--output-dir`` (по умолчанию ``data/blind_test``)::

    game_1_A.txt  game_1_B.txt  game_1_C.txt      # 3 партии × 3 уровня
    game_2_A.txt  game_2_B.txt  game_2_C.txt
    game_3_A.txt  game_3_B.txt  game_3_C.txt
    answer_form.md       # анкета для тестировщика (уровни не раскрыты)
    answers_key.json     # ключ для организатора: файл → (game_id, level)

Анонимность: имя файла — только номер партии и слот; в текст файла
дописывается ровно сгенерированный комментарий (без заголовков и меток
уровня). Уровни есть только в ``answers_key.json``, который организатор не
показывает тестировщикам; анкета и тексты комментариев уровня не раскрывают.

Запуск::

    python scripts/generate_blind_test.py --dry-run
    python scripts/generate_blind_test.py --game-ids 12,15,18
    python scripts/generate_blind_test.py --seed 35 --language ru
    python scripts/generate_blind_test.py --output-dir /tmp/blind_test

По умолчанию берутся 3 последние партии с анализом ходов (есть строки в
``moves``). ``--game-ids`` задаёт партии явно (ровно три уникальных id).
``--dry-run`` не вызывает LLM: пишет заглушки, поэтому скрипт проверяется
без LM Studio/DeepSeek. ``--language ru|en`` — язык комментариев и анкеты.

Генерация комментариев спрятана за callable-провайдером
``provider(session, game_id, level, language) -> str``: по умолчанию это
``app.commentary.service.generate_commentary`` на ``AsyncSession``, а
unit-тесты подставляют фейк и работают офлайн. ``main()`` принимает те же
зависимости параметрами (``provider``, ``load_game_ids``,
``session_factory``), чтобы CLI проверялся без БД.

Exit codes: ``0`` — успех; ``2`` — неверные аргументы, в БД меньше трёх
партий с анализом или ошибка генерации.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import exists, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.db.models import Game, Level, Move  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_SEED",
    "FORM_FILENAME",
    "GAMES_COUNT",
    "KEY_FILENAME",
    "LEVELS",
    "SCRIPT_NAME",
    "SLOTS",
    "BlindTestConfig",
    "BlindTestResult",
    "CommentaryEntry",
    "build_parser",
    "build_slot_mapping",
    "commentary_file_name",
    "default_provider",
    "dry_run_provider",
    "load_game_ids_with_analysis",
    "main",
    "plan_commentary_entries",
    "render_answer_form",
    "render_answers_key",
    "render_commentary_text",
    "run_blind_test",
    "write_blind_test",
]

#: Сколько партий в одном blind-тесте (3 уровня × 3 партии = 9 файлов).
GAMES_COUNT = 3

#: Слоты, между которыми перемешиваются уровни внутри партии.
SLOTS: tuple[str, ...] = ("A", "B", "C")

#: Уровни берём из модели, чтобы не дублировать список в скрипте.
LEVELS: tuple[str, ...] = tuple(level.value for level in Level)

#: Фиксированный seed по умолчанию: прогоны воспроизводимы.
DEFAULT_SEED = 35

DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "blind_test"
FORM_FILENAME = "answer_form.md"
KEY_FILENAME = "answers_key.json"
SCRIPT_NAME = "scripts/generate_blind_test.py"

#: Коды возврата CLI (как у scripts/prune_export_jobs.py).
EXIT_OK = 0
EXIT_ERROR = 2

#: Провайдер комментария: ``(session, game_id, level, language) -> текст``.
CommentaryProvider = Callable[
    [AsyncSession | None, int, str, str], Awaitable[str]
]

#: Загрузчик id партий (по умолчанию — 3 последние с анализом ходов).
GameIdLoader = Callable[[AsyncSession, int], Awaitable[list[int]]]

#: Фабрика сессий (в тестах подменяется на фейковую, без БД).
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class BlindTestConfig:
    """Параметры одного прогона генератора.

    Attributes:
        output_dir: куда писать 9 txt + анкету + ключ.
        seed: seed перемешивания слотов A/B/C (детерминизм).
        game_ids: явные id партий (ровно 3 уникальных) или ``None`` —
            выбрать 3 последние партии с анализом из БД.
        dry_run: не вызывать LLM, писать заглушки.
        language: ``ru``/``en`` — язык комментариев и анкеты.
    """

    output_dir: Path
    seed: int = DEFAULT_SEED
    game_ids: tuple[int, ...] | None = None
    dry_run: bool = False
    language: str = "ru"


@dataclass(frozen=True, slots=True)
class CommentaryEntry:
    """Один комментарий: нейтральное имя файла + секретные уровень/партия."""

    file_name: str
    game_index: int
    game_id: int
    level: str
    text: str


@dataclass(frozen=True, slots=True)
class BlindTestResult:
    """Результат прогона: куда и что записано (для отчёта CLI/тестов)."""

    output_dir: Path
    commentary_files: tuple[Path, ...]
    answer_form: Path
    answers_key: Path
    game_ids: tuple[int, ...]
    dry_run: bool

    def summary_lines(self) -> list[str]:
        """Человекочитаемый отчёт CLI (без раскрытия раскладки уровней)."""
        mode = " (dry-run: заглушки без LLM)" if self.dry_run else ""
        lines = [
            f"Блин-тест: {len(self.game_ids)} партии x {len(LEVELS)} уровня "
            f"= {len(self.commentary_files)} файлов{mode}",
            f"Каталог: {self.output_dir}",
            f"Анкета тестировщика: {self.answer_form.name}",
            f"Ключ организатора (не показывать тестировщику): "
            f"{self.answers_key.name}",
        ]
        lines.extend(f"  {path.name}" for path in self.commentary_files)
        return lines


# --- Чистая логика: раскладка уровней, рендеринг, запись -------------------


def build_slot_mapping(
    game_ids: Sequence[int], *, seed: int
) -> dict[int, dict[str, str]]:
    """Секретная раскладка ``game_id -> {slot: level}``.

    Для каждой партии уровни детерминированно перемешиваются seeded-RNG,
    поэтому слот ``A`` с равной вероятностью оказывается любым уровнем, а
    одинаковый ``seed`` всегда даёт одинаковую раскладку.

    Raises:
        ValueError: партий не ровно :data:`GAMES_COUNT` или id повторяются.
    """
    if len(game_ids) != GAMES_COUNT:
        raise ValueError(
            f"нужно ровно {GAMES_COUNT} партии, получено {len(game_ids)}"
        )
    if len(set(game_ids)) != len(game_ids):
        raise ValueError("id партий не должны повторяться")
    rng = random.Random(seed)
    mapping: dict[int, dict[str, str]] = {}
    for game_id in game_ids:
        shuffled = list(LEVELS)
        rng.shuffle(shuffled)
        mapping[game_id] = dict(zip(SLOTS, shuffled, strict=True))
    return mapping


def commentary_file_name(game_index: int, slot: str) -> str:
    """Нейтральное имя файла: ``game_<N>_<A|B|C>.txt`` (без уровня)."""
    return f"game_{game_index}_{slot}.txt"


def plan_commentary_entries(
    game_ids: Sequence[int],
    mapping: Mapping[int, Mapping[str, str]],
    texts: Mapping[int, Mapping[str, str]],
) -> list[CommentaryEntry]:
    """Сопоставить сгенерированные тексты файлам по секретной раскладке.

    Args:
        game_ids: id партий в порядке нумерации ``game_1..game_3``.
        mapping: результат :func:`build_slot_mapping`.
        texts: ``game_id -> {level: text}`` (все 3 уровня на партию).
    """
    entries: list[CommentaryEntry] = []
    for index, game_id in enumerate(game_ids, start=1):
        for slot in SLOTS:
            level = mapping[game_id][slot]
            entries.append(
                CommentaryEntry(
                    file_name=commentary_file_name(index, slot),
                    game_index=index,
                    game_id=game_id,
                    level=level,
                    text=texts[game_id][level],
                )
            )
    return entries


def render_commentary_text(text: str) -> str:
    """Тело txt-файла: текст комментария и ровно один перевод строки.

    Никаких заголовков/меток уровня не добавляем: иначе анонимность
    blind-теста ломается.

    Raises:
        ValueError: провайдер вернул пустой текст.
    """
    body = text.strip()
    if not body:
        raise ValueError("провайдер вернул пустой текст комментария")
    return body + "\n"


def render_answers_key(
    entries: Sequence[CommentaryEntry],
    game_ids: Sequence[int],
    *,
    seed: int,
    language: str,
    generated_at: str,
) -> dict[str, Any]:
    """Ключ организатора: ``файл -> {game_id, level}`` + параметры прогона."""
    files = {
        entry.file_name: {"game_id": entry.game_id, "level": entry.level}
        for entry in entries
    }
    games = [
        {"index": entry.game_index, "game_id": entry.game_id}
        for entry in entries
        if entry.file_name == commentary_file_name(entry.game_index, SLOTS[0])
    ]
    return {
        "schema_version": 1,
        "script": SCRIPT_NAME,
        "generated_at": generated_at,
        "seed": seed,
        "language": language,
        "levels": list(LEVELS),
        "games": games,
        "files": files,
    }


def _game_file_names(
    entries: Sequence[CommentaryEntry], game_index: int
) -> list[str]:
    """Имена файлов одной партии в нейтральном порядке A, B, C."""
    return sorted(
        entry.file_name
        for entry in entries
        if entry.game_index == game_index
    )


def _render_file_rows(file_names: Sequence[str]) -> list[str]:
    """Строки markdown-таблицы анкеты: файл, порядок, качество, замечания."""
    return [
        f"| `{name}` |  |  |  |" for name in file_names
    ]


def _render_form_ru(
    entries: Sequence[CommentaryEntry],
    *,
    seed: int,
    language: str,
    generated_at: str,
) -> str:
    """Русская анкета: инструкции + таблица на каждую партию."""
    lines = [
        "# Блин-тест комментариев ChessAnnotator",
        "",
        f"Сгенерировано: {generated_at}",
        f"Скрипт: `{SCRIPT_NAME}`, seed: {seed}, язык комментариев: "
        f"{language}",
        "",
        "## Как заполнять",
        "",
        f"Вам даны {len(entries) // len(LEVELS)} партии. Для каждой — три "
        "комментария в файлах",
        "`game_N_A.txt`, `game_N_B.txt`, `game_N_C.txt`. Порядок букв A/B/C",
        "намеренно перемешан и не связан с уровнем комментария.",
        "",
        "1. Прочитайте все три файла одной партии.",
        "2. Расставьте их по порядку «от самого простого к самому сложному»:",
        f"   1 — самый простой, {len(LEVELS)} — самый сложный.",
        "3. Оцените качество каждого комментария по шкале 1–10.",
        "4. Добавьте замечания, если есть.",
        "",
        f"Файл `{KEY_FILENAME}` — ключ организатора; не открывайте его до",
        "заполнения анкеты, иначе тест перестаёт быть слепым.",
    ]
    game_indexes = sorted({entry.game_index for entry in entries})
    for game_index in game_indexes:
        lines.extend(
            [
                "",
                f"## Партия {game_index}",
                "",
                "| Файл | Порядок (1–3) | Качество (1–10) | Замечания |",
                "| --- | --- | --- | --- |",
                *_render_file_rows(_game_file_names(entries, game_index)),
            ]
        )
    return "\n".join(lines) + "\n"


def _render_form_en(
    entries: Sequence[CommentaryEntry],
    *,
    seed: int,
    language: str,
    generated_at: str,
) -> str:
    """English answer form: instructions + one table per game."""
    lines = [
        "# ChessAnnotator blind commentary test",
        "",
        f"Generated: {generated_at}",
        f"Script: `{SCRIPT_NAME}`, seed: {seed}, commentary language: "
        f"{language}",
        "",
        "## How to fill in",
        "",
        f"You have {len(entries) // len(LEVELS)} games. Each game has three "
        "commentaries in files",
        "`game_N_A.txt`, `game_N_B.txt`, `game_N_C.txt`. The A/B/C order is",
        "shuffled on purpose and does not correlate with the commentary "
        "level.",
        "",
        "1. Read all three files of one game.",
        "2. Rank them from the simplest to the most advanced:",
        f"   1 — the simplest, {len(LEVELS)} — the most advanced.",
        "3. Rate each commentary from 1 to 10.",
        "4. Add notes if you have any.",
        "",
        f"`{KEY_FILENAME}` is the organizer's answer key; do not open it",
        "before filling in the form, otherwise the test is no longer blind.",
    ]
    game_indexes = sorted({entry.game_index for entry in entries})
    for game_index in game_indexes:
        lines.extend(
            [
                "",
                f"## Game {game_index}",
                "",
                "| File | Rank (1–3) | Quality (1–10) | Notes |",
                "| --- | --- | --- | --- |",
                *_render_file_rows(_game_file_names(entries, game_index)),
            ]
        )
    return "\n".join(lines) + "\n"


def render_answer_form(
    entries: Sequence[CommentaryEntry],
    *,
    seed: int,
    language: str,
    generated_at: str,
) -> str:
    """Markdown-анкета тестировщика; уровни файлов не раскрываются."""
    if language == "en":
        return _render_form_en(
            entries,
            seed=seed,
            language=language,
            generated_at=generated_at,
        )
    return _render_form_ru(
        entries, seed=seed, language=language, generated_at=generated_at
    )


def write_blind_test(
    output_dir: Path,
    entries: Sequence[CommentaryEntry],
    *,
    answer_form: str,
    answers_key: Mapping[str, Any],
) -> tuple[Path, Path, list[Path]]:
    """Записать 9 txt-файлов, анкету и ключ; вернуть ``(анкета, ключ, txt)``.

    Каталог создаётся при необходимости. Старые файлы с другими именами не
    удаляются: для чистого прогона используйте пустой ``--output-dir``.
    """
    root = output_dir.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    # Сначала проверяем все тексты: не оставляем частичный набор файлов,
    # если один из комментариев оказался пустым.
    bodies = [
        (root / entry.file_name, render_commentary_text(entry.text))
        for entry in entries
    ]
    commentary_paths: list[Path] = []
    for path, body in bodies:
        path.write_text(body, encoding="utf-8")
        commentary_paths.append(path)
    form_path = root / FORM_FILENAME
    form_path.write_text(answer_form, encoding="utf-8")
    key_path = root / KEY_FILENAME
    key_path.write_text(
        json.dumps(answers_key, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return form_path, key_path, commentary_paths


# --- Провайдеры комментариев ------------------------------------------------


async def dry_run_provider(
    session: AsyncSession | None,
    game_id: int,
    level: str,
    language: str,
) -> str:
    """Заглушка для ``--dry-run``: LLM не вызывается, уровень не упоминается."""
    del session, level  # dry-run не использует ни БД, ни уровень
    if language == "en":
        return f"[DRY-RUN] Placeholder commentary for game {game_id}."
    return f"[DRY-RUN] Заглушка комментария для партии {game_id}."


async def default_provider(
    session: AsyncSession | None,
    game_id: int,
    level: str,
    language: str,
) -> str:
    """Реальный провайдер: `generate_commentary` на переданной сессии.

    Импорт тяжёлого LLM-стека — внутри функции, чтобы ``--dry-run`` и
    unit-тесты не поднимали клиентов моделей.
    """
    if session is None:
        raise RuntimeError("для реальной генерации нужна сессия БД")
    from app.commentary.service import generate_commentary

    generated = await generate_commentary(
        session, game_id, level, language=language
    )
    return generated.text


async def load_game_ids_with_analysis(
    session: AsyncSession, limit: int = GAMES_COUNT
) -> list[int]:
    """3 последние партии, у которых есть анализ ходов (строки ``moves``)."""
    has_moves = exists(select(Move.id).where(Move.game_id == Game.id))
    rows = await session.scalars(
        select(Game.id)
        .where(has_moves)
        .order_by(Game.created_at.desc(), Game.id.desc())
        .limit(limit)
    )
    return [int(game_id) for game_id in rows]


# --- Оркестрация и CLI ------------------------------------------------------


def _validate_config(config: BlindTestConfig) -> None:
    """Проверить параметры прогона (то же, что проверяет argparse)."""
    if config.game_ids is not None:
        if len(config.game_ids) != GAMES_COUNT:
            raise ValueError(
                f"--game-ids: нужно ровно {GAMES_COUNT} id, "
                f"получено {len(config.game_ids)}"
            )
        if any(game_id <= 0 for game_id in config.game_ids):
            raise ValueError("--game-ids: id должны быть > 0")
        if len(set(config.game_ids)) != len(config.game_ids):
            raise ValueError("--game-ids: id не должны повторяться")
    if config.language not in {"ru", "en"}:
        raise ValueError("--language: поддерживаются ru и en")


async def _select_game_ids(
    factory: SessionFactory,
    loader: GameIdLoader,
) -> list[int]:
    """Выбрать партии из БД, когда ``--game-ids`` не задан."""
    async with factory() as session:
        return await loader(session, GAMES_COUNT)


async def run_blind_test(
    config: BlindTestConfig,
    *,
    provider: CommentaryProvider | None = None,
    load_game_ids: GameIdLoader | None = None,
    session_factory: SessionFactory | None = None,
    now: datetime | None = None,
) -> BlindTestResult:
    """Сгенерировать blind-тест и записать файлы (главная async-логика).

    Args:
        config: параметры прогона.
        provider: провайдер комментариев; ``None`` — реальная генерация
            (или :func:`dry_run_provider` при ``config.dry_run``).
        load_game_ids: загрузчик id партий; ``None`` — 3 последние с
            анализом из БД.
        session_factory: фабрика ``AsyncSession`` (в тестах — фейковая).
        now: момент генерации (для детерминированных тестов).

    Raises:
        ValueError: неверные параметры или в БД меньше трёх партий с
            анализом.
    """
    _validate_config(config)
    factory: SessionFactory = session_factory or SessionLocal
    loader: GameIdLoader = load_game_ids or load_game_ids_with_analysis

    if config.game_ids is not None:
        game_ids = list(config.game_ids)
    else:
        game_ids = await _select_game_ids(factory, loader)
    if len(game_ids) < GAMES_COUNT:
        raise ValueError(
            f"нужно {GAMES_COUNT} партии с анализом ходов, "
            f"в БД найдено {len(game_ids)}"
        )
    game_ids = game_ids[:GAMES_COUNT]
    mapping = build_slot_mapping(game_ids, seed=config.seed)
    active_provider: CommentaryProvider = provider or (
        dry_run_provider if config.dry_run else default_provider
    )

    texts: dict[int, dict[str, str]] = {}
    if config.dry_run:
        for game_id in game_ids:
            texts[game_id] = {
                level: await active_provider(None, game_id, level, config.language)
                for level in LEVELS
            }
    else:
        async with factory() as session:
            for game_id in game_ids:
                texts[game_id] = {
                    level: await active_provider(
                        session, game_id, level, config.language
                    )
                    for level in LEVELS
                }

    entries = plan_commentary_entries(game_ids, mapping, texts)
    generated_at = (now or datetime.now(UTC)).isoformat()
    answer_form = render_answer_form(
        entries,
        seed=config.seed,
        language=config.language,
        generated_at=generated_at,
    )
    answers_key = render_answers_key(
        entries,
        game_ids,
        seed=config.seed,
        language=config.language,
        generated_at=generated_at,
    )
    form_path, key_path, commentary_paths = write_blind_test(
        config.output_dir,
        entries,
        answer_form=answer_form,
        answers_key=answers_key,
    )
    return BlindTestResult(
        output_dir=config.output_dir.expanduser(),
        commentary_files=tuple(commentary_paths),
        answer_form=form_path,
        answers_key=key_path,
        game_ids=tuple(game_ids),
        dry_run=config.dry_run,
    )


def _game_ids_arg(value: str) -> tuple[int, ...]:
    """argparse-тип ``--game-ids``: ровно 3 уникальных положительных id."""
    raw_parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(raw_parts) != GAMES_COUNT:
        raise argparse.ArgumentTypeError(
            f"нужно ровно {GAMES_COUNT} id через запятую, "
            f"получено {len(raw_parts)}"
        )
    try:
        game_ids = tuple(int(part) for part in raw_parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"id должны быть целыми числами: {value!r}"
        ) from exc
    if any(game_id <= 0 for game_id in game_ids):
        raise argparse.ArgumentTypeError("id должны быть > 0")
    if len(set(game_ids)) != len(game_ids):
        raise argparse.ArgumentTypeError("id не должны повторяться")
    return game_ids


def build_parser() -> argparse.ArgumentParser:
    """CLI-парсер генератора blind-теста."""
    parser = argparse.ArgumentParser(
        prog="generate_blind_test.py",
        description=(
            "Слепой тест комментариев (T-024): 3 партии × 3 уровня → "
            "анонимные txt + анкета + ключ организатора."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        metavar="PATH",
        help=(
            "каталог результатов "
            f"(по умолчанию {DEFAULT_OUTPUT_DIR})"
        ),
    )
    parser.add_argument(
        "--game-ids",
        type=_game_ids_arg,
        default=None,
        metavar="ID1,ID2,ID3",
        help=(
            "явные id трёх партий через запятую; по умолчанию — 3 последние "
            "партии с анализом ходов из БД"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        metavar="N",
        help=f"seed перемешивания слотов A/B/C (по умолчанию {DEFAULT_SEED})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="не вызывать LLM: записать тексты-заглушки",
    )
    parser.add_argument(
        "--language",
        choices=("ru", "en"),
        default="ru",
        help="язык комментариев и анкеты (по умолчанию ru)",
    )
    return parser


async def _run_cli(
    args: argparse.Namespace,
    *,
    provider: CommentaryProvider | None,
    load_game_ids: GameIdLoader | None,
    session_factory: SessionFactory | None,
) -> int:
    """Выполнить прогон из CLI-аргументов; вернуть exit code."""
    config = BlindTestConfig(
        output_dir=args.output_dir,
        seed=args.seed,
        game_ids=tuple(args.game_ids) if args.game_ids is not None else None,
        dry_run=args.dry_run,
        language=args.language,
    )
    try:
        result = await run_blind_test(
            config,
            provider=provider,
            load_game_ids=load_game_ids,
            session_factory=session_factory,
        )
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # сеть/LLM/БД: понятная ошибка вместо traceback
        print(f"Ошибка генерации блин-теста: {exc}", file=sys.stderr)
        return EXIT_ERROR

    for line in result.summary_lines():
        print(line)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    provider: CommentaryProvider | None = None,
    load_game_ids: GameIdLoader | None = None,
    session_factory: SessionFactory | None = None,
) -> int:
    """Точка входа CLI: вернуть exit code (argparse сам даёт 2 на ошибках).

    Параметры ``provider``/``load_game_ids``/``session_factory`` — точки
    внедрения для тестов: позволяют прогнать CLI без LLM и без БД.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return asyncio.run(
        _run_cli(
            args,
            provider=provider,
            load_game_ids=load_game_ids,
            session_factory=session_factory,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
