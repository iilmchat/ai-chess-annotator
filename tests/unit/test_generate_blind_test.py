"""Unit-тесты генератора blind-теста комментариев (T-024), без LLM и БД.

Проверяем чистую логику: seeded-раскладку уровней по слотам A/B/C,
анонимность (маркеры уровня не попадают в имена файлов, тексты и анкету),
рендер анкеты и ключа организатора, запись 9 + 2 файлов, детерминизм и CLI.

Генерация комментариев спрятана за callable-провайдером, выборка партий — за
callable-загрузчиком, сессия — за фабрикой: поэтому весь модуль работает
офлайн, без LM Studio/DeepSeek и без PostgreSQL.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.db.models import Level
from scripts import generate_blind_test
from scripts.generate_blind_test import (
    BlindTestConfig,
    build_parser,
    build_slot_mapping,
    commentary_file_name,
    main,
    render_commentary_text,
    run_blind_test,
)

GAME_IDS = (11, 22, 33)
NEUTRAL_TEXT = "Нейтральный комментарий про ходы и позицию."

#: Маркеры уровня: их не должно быть в именах/текстах файлов и в анкете.
LEVEL_TOKEN = re.compile(
    r"\b(beginner|club|gm|гроссмейстер\w*|новичок\w*|клубн\w*)\b",
    re.IGNORECASE,
)

#: Имена 9 файлов комментариев в нейтральном порядке.
EXPECTED_FILES = [
    commentary_file_name(index, slot)
    for index in range(1, generate_blind_test.GAMES_COUNT + 1)
    for slot in generate_blind_test.SLOTS
]


@dataclass
class FakeProvider:
    """Фейковый провайдер: нейтральный текст + журнал вызовов."""

    text: str = NEUTRAL_TEXT
    calls: list[tuple[int, str, str]] = field(default_factory=list)

    async def __call__(
        self, session: object, game_id: int, level: str, language: str
    ) -> str:
        self.calls.append((game_id, level, language))
        return f"{self.text} #{game_id}"


@dataclass
class FakeGameLoader:
    """Фейковый загрузчик партий: подменяет выборку из БД."""

    game_ids: list[int]
    limit: int | None = None

    async def __call__(self, session: object, limit: int) -> list[int]:
        self.limit = limit
        return self.game_ids[:limit]


@asynccontextmanager
async def _fake_session_factory() -> AsyncIterator[object]:
    """Фейковому провайдеру сессия не нужна — отдаём заглушку."""
    yield object()


class _EmptyProvider:
    """Провайдер, который «сломался» и вернул пустой текст."""

    async def __call__(
        self, session: object, game_id: int, level: str, language: str
    ) -> str:
        return "   \n"


def _config(
    output_dir: Path,
    *,
    seed: int = generate_blind_test.DEFAULT_SEED,
    game_ids: tuple[int, ...] = GAME_IDS,
    dry_run: bool = False,
) -> BlindTestConfig:
    return BlindTestConfig(
        output_dir=output_dir,
        seed=seed,
        game_ids=game_ids,
        dry_run=dry_run,
    )


def _read_key(output_dir: Path) -> dict[str, object]:
    raw = (output_dir / generate_blind_test.KEY_FILENAME).read_text(
        encoding="utf-8"
    )
    data = json.loads(raw)
    assert isinstance(data, dict)
    return data


def _read_form(output_dir: Path) -> str:
    return (output_dir / generate_blind_test.FORM_FILENAME).read_text(
        encoding="utf-8"
    )


def _txt_names(output_dir: Path) -> list[str]:
    return sorted(path.name for path in output_dir.glob("game_*.txt"))


def _assert_anonymized(root: Path) -> None:
    """Ни имя, ни текст файлов (кроме ключа) не содержат маркеров уровня."""
    paths = [
        path
        for path in root.iterdir()
        if path.name != generate_blind_test.KEY_FILENAME
    ]
    assert paths
    for path in paths:
        assert not LEVEL_TOKEN.search(path.name), path.name
        assert not LEVEL_TOKEN.search(
            path.read_text(encoding="utf-8")
        ), path.name


# --- Раскладка уровней по слотам -------------------------------------------


def test_slot_mapping_is_a_permutation_per_game() -> None:
    mapping = build_slot_mapping(GAME_IDS, seed=generate_blind_test.DEFAULT_SEED)

    assert set(mapping) == set(GAME_IDS)
    expected_levels = {level.value for level in Level}
    for slots in mapping.values():
        assert set(slots) == set(generate_blind_test.SLOTS)
        assert set(slots.values()) == expected_levels


def test_slot_mapping_is_deterministic_for_same_seed() -> None:
    first = build_slot_mapping(GAME_IDS, seed=35)
    second = build_slot_mapping(GAME_IDS, seed=35)
    other = build_slot_mapping(GAME_IDS, seed=36)

    assert first == second
    # Другой seed не обязан менять все партии, но полная раскладка отличается.
    assert other != first


@pytest.mark.parametrize(
    "game_ids",
    [
        (),
        (11,),
        (11, 22),
        (11, 22, 33, 44),
        (11, 22, 22),
    ],
)
def test_slot_mapping_rejects_bad_game_ids(
    game_ids: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match=r"партии|повторяться"):
        build_slot_mapping(game_ids, seed=35)


# --- Анонимизация текста ----------------------------------------------------


def test_render_commentary_text_normalises_body() -> None:
    assert render_commentary_text("  текст\n\n") == "текст\n"


def test_render_commentary_text_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="пустой"):
        render_commentary_text("   \n")


def test_dry_run_provider_mentions_game_but_not_level() -> None:
    ru_text = asyncio.run(
        generate_blind_test.dry_run_provider(None, 7, Level.gm.value, "ru")
    )
    en_text = asyncio.run(
        generate_blind_test.dry_run_provider(None, 7, Level.gm.value, "en")
    )

    assert "7" in ru_text
    assert "game 7" in en_text
    assert not LEVEL_TOKEN.search(ru_text)
    assert not LEVEL_TOKEN.search(en_text)


# --- Полный прогон (фейковые провайдер и сессия) ----------------------------


def test_run_blind_test_writes_anonymized_set(tmp_path: Path) -> None:
    provider = FakeProvider()

    result = asyncio.run(
        run_blind_test(
            _config(tmp_path),
            provider=provider,
            session_factory=_fake_session_factory,
        )
    )

    assert result.dry_run is False
    assert len(result.commentary_files) == 9
    assert _txt_names(tmp_path) == sorted(EXPECTED_FILES)
    assert (tmp_path / generate_blind_test.FORM_FILENAME).is_file()
    assert (tmp_path / generate_blind_test.KEY_FILENAME).is_file()

    # Все 9 комбинаций (партия, уровень) сгенерированы ровно по разу.
    expected_calls = {
        (game_id, level)
        for game_id in GAME_IDS
        for level in generate_blind_test.LEVELS
    }
    assert len(provider.calls) == 9
    assert {(game_id, level) for game_id, level, _ in provider.calls} == (
        expected_calls
    )

    # Текст файла — нейтральный ответ провайдера для партии из ключа.
    key = _read_key(tmp_path)
    files = key["files"]
    assert isinstance(files, dict)
    # Ключ покрывает каждый файл ровно один раз и ссылается на валидный уровень.
    assert set(files) == set(EXPECTED_FILES)
    assert {info["game_id"] for info in files.values()} == set(GAME_IDS)
    for name in EXPECTED_FILES:
        info = files[name]
        assert set(info) == {"game_id", "level"}
        assert info["level"] in generate_blind_test.LEVELS
        expected_text = f"{NEUTRAL_TEXT} #{info['game_id']}\n"
        assert (tmp_path / name).read_text(encoding="utf-8") == expected_text

    _assert_anonymized(tmp_path)


def test_each_game_gets_all_three_levels_exactly_once(tmp_path: Path) -> None:
    asyncio.run(run_blind_test(_config(tmp_path, dry_run=True)))

    files = _read_key(tmp_path)["files"]
    assert isinstance(files, dict)
    expected_levels = sorted(generate_blind_test.LEVELS)
    for index in range(1, generate_blind_test.GAMES_COUNT + 1):
        levels = sorted(
            info["level"]
            for name, info in files.items()
            if name.startswith(f"game_{index}_")
        )
        assert levels == expected_levels, index


def test_empty_commentary_aborts_without_partial_files(
    tmp_path: Path,
) -> None:
    """Пустой текст провайдера — ошибка, а не набор полупустых файлов."""
    with pytest.raises(ValueError, match="пустой"):
        asyncio.run(
            run_blind_test(
                _config(tmp_path),
                provider=_EmptyProvider(),
                session_factory=_fake_session_factory,
            )
        )

    assert list(tmp_path.iterdir()) == []


def test_same_seed_produces_same_mapping(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    asyncio.run(run_blind_test(_config(first_dir, dry_run=True)))
    asyncio.run(run_blind_test(_config(second_dir, dry_run=True)))

    assert _read_key(first_dir)["files"] == _read_key(second_dir)["files"]
    assert _read_key(first_dir)["games"] == _read_key(second_dir)["games"]


def test_answer_form_lists_files_without_levels(tmp_path: Path) -> None:
    result = asyncio.run(run_blind_test(_config(tmp_path, dry_run=True)))

    form = result.answer_form.read_text(encoding="utf-8")
    for name in EXPECTED_FILES:
        assert f"`{name}`" in form
    assert "Порядок" in form
    assert "Качество" in form
    # Организатору важно знать, каким скриптом сделан прогон.
    assert "scripts/generate_blind_test.py" in form
    assert not LEVEL_TOKEN.search(form)
    assert generate_blind_test.KEY_FILENAME in form


def test_blank_search_form_marks_every_file_row(tmp_path: Path) -> None:
    """На каждый файл — отдельная пустая строка таблицы (1/2/3, 1–10)."""
    asyncio.run(run_blind_test(_config(tmp_path, dry_run=True)))

    form = _read_form(tmp_path)
    for name in EXPECTED_FILES:
        assert f"| `{name}` |  |  |  |" in form
    assert form.count("## Партия") == generate_blind_test.GAMES_COUNT


# --- CLI --------------------------------------------------------------------


def test_cli_dry_run_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--dry-run",
            "--game-ids",
            "11,22,33",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "9 файлов" in captured.out
    assert "dry-run" in captured.out
    assert _txt_names(tmp_path) == sorted(EXPECTED_FILES)
    key = _read_key(tmp_path)
    assert key["games"] == [
        {"index": 1, "game_id": 11},
        {"index": 2, "game_id": 22},
        {"index": 3, "game_id": 33},
    ]
    assert "DRY-RUN" in (tmp_path / "game_1_A.txt").read_text(
        encoding="utf-8"
    )
    _assert_anonymized(tmp_path)


def test_cli_uses_injected_db_loader_and_session(tmp_path: Path) -> None:
    """Путь «--game-ids нет»: партии выбирает внедрённый загрузчик (не БД)."""
    loader = FakeGameLoader([101, 102, 103])

    code = main(
        ["--dry-run", "--output-dir", str(tmp_path)],
        load_game_ids=loader,
        session_factory=_fake_session_factory,
    )

    assert code == 0
    assert loader.limit == generate_blind_test.GAMES_COUNT
    key = _read_key(tmp_path)
    games = key["games"]
    assert isinstance(games, list)
    assert [game["game_id"] for game in games] == [101, 102, 103]


def test_cli_reports_too_few_games(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["--dry-run", "--output-dir", str(tmp_path)],
        load_game_ids=FakeGameLoader([101]),
        session_factory=_fake_session_factory,
    )

    assert code == 2
    assert "нужно 3" in capsys.readouterr().err
    assert _txt_names(tmp_path) == []


def test_cli_en_form_is_english(tmp_path: Path) -> None:
    provider = FakeProvider()

    code = main(
        [
            "--game-ids",
            "11,22,33",
            "--language",
            "en",
            "--output-dir",
            str(tmp_path),
        ],
        provider=provider,
        session_factory=_fake_session_factory,
    )

    assert code == 0
    assert {language for _, _, language in provider.calls} == {"en"}
    assert "How to fill in" in _read_form(tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["--game-ids", "1,2"],
        ["--game-ids", "1,2,2"],
        ["--game-ids", "1,2,x"],
        ["--game-ids", "0,1,2"],
        ["--seed", "not-a-number"],
        ["--language", "de"],
    ],
)
def test_cli_rejects_invalid_args_with_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code == 2


def test_run_rejects_invalid_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="language"):
        asyncio.run(
            run_blind_test(
                BlindTestConfig(
                    output_dir=tmp_path, game_ids=GAME_IDS, language="de"
                )
            )
        )
    with pytest.raises(ValueError, match="ровно 3"):
        asyncio.run(
            run_blind_test(
                BlindTestConfig(output_dir=tmp_path, game_ids=(1, 2))
            )
        )
