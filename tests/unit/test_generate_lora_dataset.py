"""Unit-тесты сборщика LoRA-датасета из БД (T-080), без PostgreSQL.

Проверяем контракт v2 (его читает ``scripts/train_lora.py``), фильтры,
детерминированную выборку, счётчики пропусков, ``--dry-run`` и exit code 2
на пустой БД. Сессия и сборщик записи подменяются фейками — как в
``tests/unit/test_generate_blind_test.py``.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.engine.models import MoveAnalysis
from app.ingestion import parse_pgn
from scripts import export_dataset
from scripts.export_dataset import load_system_prompt
from scripts.generate_lora_dataset_from_db import (
    DEFAULT_LIMIT,
    SCHEMA_VERSION,
    DatasetConfig,
    GenerationResult,
    GenerationStats,
    RecordSkippedError,
    build_parser,
    build_record,
    filter_candidates,
    main,
    run_export,
    sample_records,
)
from scripts.train_lora import validate_dataset

FACT_PACKAGE = (
    "# Партия\nБелые: A\nЧёрные: B\n\n"
    "# Ходы партии (SAN)\n1. e4 e5\n\n"
    "# Ключевые моменты (до 3, по убыванию потери)\n"
    "## 1. Ход 1 белых: e4 (полуход 1)\n- Сыграно: e4 (белые)"
)
SHORT_PGN = "1. e4 e5 2. Nf3 Nc6"
CREATED_AT = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _edit(
    edit_id: int = 7,
    *,
    text: str = "правка редактора",
    created_at: datetime = CREATED_AT,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=edit_id,
        commentary_id=edit_id + 100,
        editor_id=1,
        original_text="исходный комментарий",
        edited_text=text,
        created_at=created_at,
    )


def _commentary(
    level: str = "club", *, game_id: int = 42
) -> SimpleNamespace:
    return SimpleNamespace(
        level=SimpleNamespace(value=level),
        model="qwen",
        game_id=game_id,
    )


def _game(pgn: str = SHORT_PGN, *, game_id: int = 42) -> SimpleNamespace:
    return SimpleNamespace(id=game_id, pgn=pgn)


def _analyses(pgn: str = SHORT_PGN) -> list[MoveAnalysis]:
    """Минимальный анализ ходов, достаточный для пакета фактов."""
    parsed = parse_pgn(pgn)
    uci = ("e2e4", "e7e5", "g1f3", "b8c6")
    return [
        MoveAnalysis(
            ply=move.ply,
            played_move_san=move.san,
            played_move_uci=uci[move.ply - 1],
            eval_before=10,
            eval_after=10,
            best_move_uci=uci[move.ply - 1],
            best_move_san=move.san,
            delta_cp=0,
            classification="good",
            engine_depth=12,
        )
        for move in parsed.moves
    ]


def _built(
    *,
    level: str = "club",
    text: str = "правка редактора",
    min_words: int = 0,
) -> dict[str, Any]:
    return build_record(
        _edit(text=text),
        _commentary(level),
        _game(),
        _analyses(),
        min_words=min_words,
    )


def _candidate(
    edit_id: int,
    *,
    level: str = "club",
    text: str = "правка редактора",
) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    return (_edit(edit_id, text=text), _commentary(level), _game())


class FakeResult:
    """Заглушка ``Result`` SQLAlchemy: только ``.all()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


@dataclass
class FakeSession:
    """Фейковая сессия: возвращает заранее заданные строки."""

    rows: list[Any]

    async def execute(self, statement: object) -> FakeResult:
        return FakeResult(self.rows)


def _session_factory(
    rows: list[Any],
) -> Any:
    """Фабрика фейковых сессий с общими строками (как ``SessionLocal``)."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[FakeSession]:
        yield FakeSession(rows)

    return factory


def _fake_builder(
    skip: dict[int, str] | None = None,
) -> Any:
    """Фейковый сборщик: v2-запись через ``record_from_edit`` или пропуск."""

    async def builder(
        session: object,
        edit: SimpleNamespace,
        commentary: SimpleNamespace,
        game: object,
        *,
        min_words: int = 0,
    ) -> dict[str, Any]:
        if skip and edit.id in skip:
            raise RecordSkippedError(skip[edit.id])
        return export_dataset.record_from_edit(
            edit, commentary, fact_package=FACT_PACKAGE
        )

    return builder


def _config(
    output: Path,
    *,
    limit: int = 10,
    level: str | None = None,
    min_words: int = 0,
    seed: int | None = None,
    dry_run: bool = False,
) -> DatasetConfig:
    return DatasetConfig(
        output=output,
        limit=limit,
        level=level,
        min_words=min_words,
        seed=seed,
        dry_run=dry_run,
    )


# --- Контракт v2 ------------------------------------------------------------


def test_build_record_matches_v2_contract() -> None:
    record = _built()

    assert set(record) == {"messages", "schema_version", "metadata"}
    assert record["schema_version"] == SCHEMA_VERSION == 2
    messages = record["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert messages[0]["content"] == load_system_prompt("club")
    assert "# Партия" in messages[1]["content"]
    assert "# Ключевые моменты" in messages[1]["content"]
    assert messages[2]["content"] == "правка редактора"

    metadata = record["metadata"]
    assert set(metadata) == {
        "id",
        "commentary_id",
        "game_id",
        "editor_id",
        "level",
        "model",
        "created_at",
        "fact_sheet_chars",
    }
    assert metadata["id"] == 7
    assert metadata["commentary_id"] == 107
    assert metadata["game_id"] == 42
    assert metadata["level"] == "club"
    assert metadata["fact_sheet_chars"] == len(messages[1]["content"])


def test_build_record_uses_level_prompt_and_editor_text() -> None:
    record = _built(level="gm", text="  правка гроссмейстера  ")

    assert record["messages"][0]["content"] == load_system_prompt("gm")
    assert record["messages"][2]["content"] == "  правка гроссмейстера  "
    assert record["metadata"]["level"] == "gm"


# --- Пропуски ---------------------------------------------------------------


def test_build_record_skips_missing_game() -> None:
    with pytest.raises(RecordSkippedError) as exc_info:
        build_record(_edit(), _commentary(), None, _analyses())

    assert exc_info.value.reason == "no_game"


def test_build_record_skips_invalid_pgn() -> None:
    with pytest.raises(RecordSkippedError) as exc_info:
        build_record(
            _edit(),
            _commentary(),
            _game("это не PGN"),
            _analyses(),
        )

    assert exc_info.value.reason == "invalid_pgn"


def test_build_record_skips_without_analysis() -> None:
    with pytest.raises(RecordSkippedError) as exc_info:
        build_record(_edit(), _commentary(), _game(), [])

    assert exc_info.value.reason == "no_analysis"


def test_build_record_skips_invalid_level() -> None:
    commentary = _commentary("master")

    with pytest.raises(RecordSkippedError) as exc_info:
        build_record(_edit(), commentary, _game(), _analyses())

    assert exc_info.value.reason == "invalid_level"


def test_min_words_filters_short_edit() -> None:
    with pytest.raises(RecordSkippedError) as exc_info:
        _built(text="два слова", min_words=3)

    assert exc_info.value.reason == "min_words"


def test_min_words_accepts_long_enough_edit() -> None:
    record = _built(text="ровно три слова", min_words=3)

    assert record["messages"][2]["content"] == "ровно три слова"


# --- Выборка и фильтры ------------------------------------------------------


def test_sample_records_is_deterministic_with_seed() -> None:
    values = list(range(20))

    first = sample_records(values, limit=5, seed=35)
    second = sample_records(values, limit=5, seed=35)
    other = sample_records(values, limit=5, seed=36)

    assert first == second
    assert first == sorted(first)
    assert len(first) == 5
    assert other != first


def test_sample_records_without_seed_takes_newest_first() -> None:
    values = list(range(20))

    assert sample_records(values, limit=5) == [0, 1, 2, 3, 4]


def test_sample_records_returns_all_when_limit_is_large() -> None:
    values = list(range(5))

    assert sample_records(values, limit=10, seed=42) == values


def test_filter_candidates_by_level_and_min_words() -> None:
    candidates = [
        _candidate(1, level="club", text="одно два три"),
        _candidate(2, level="gm", text="одно два три"),
        _candidate(3, level="club", text="одно"),
    ]

    filtered = filter_candidates(candidates, level="club", min_words=2)

    assert [edit.id for edit, _, _ in filtered] == [1]


# --- Полный прогон с фейковыми сессией и сборщиком --------------------------


def _rows(*ids: int, level: str = "club") -> list[Any]:
    return [_candidate(edit_id, level=level) for edit_id in ids]


def test_run_export_respects_limit_and_level(tmp_path: Path) -> None:
    rows = [
        _candidate(1, level="club"),
        _candidate(2, level="club"),
        _candidate(3, level="gm"),
        _candidate(4, level="club"),
    ]
    output = tmp_path / "dataset.jsonl"

    result = asyncio.run(
        run_export(
            _config(output, limit=2, level="club"),
            session_factory=_session_factory(rows),
            record_builder=_fake_builder(),
        )
    )

    assert result.stats.candidates == 3
    assert result.stats.exported == 2
    assert result.stats.levels == Counter({"club": 2})
    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_run_export_counts_skip_reasons(tmp_path: Path) -> None:
    rows = _rows(1, 2, 3)
    output = tmp_path / "dataset.jsonl"

    result = asyncio.run(
        run_export(
            _config(output, limit=10),
            session_factory=_session_factory(rows),
            record_builder=_fake_builder(
                {2: "no_analysis", 3: "invalid_pgn"}
            ),
        )
    )

    assert result.stats.exported == 1
    assert result.stats.skipped_total == 2
    assert result.stats.skipped == Counter(
        {"no_analysis": 1, "invalid_pgn": 1}
    )
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_run_export_dry_run_does_not_write_file(tmp_path: Path) -> None:
    output = tmp_path / "dataset.jsonl"

    result = asyncio.run(
        run_export(
            _config(output, dry_run=True),
            session_factory=_session_factory(_rows(1, 2)),
            record_builder=_fake_builder(),
        )
    )

    assert result.stats.exported == 2
    assert result.dry_run is True
    assert not output.exists()
    assert any("--dry-run" in line for line in result.summary_lines())


def test_output_is_one_json_object_per_line_and_train_lora_reads_it(
    tmp_path: Path,
) -> None:
    output = tmp_path / "dataset.jsonl"

    asyncio.run(
        run_export(
            _config(output),
            session_factory=_session_factory(_rows(1, 2, 3)),
            record_builder=_fake_builder(),
        )
    )

    raw_lines = output.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3
    for line in raw_lines:
        record = json.loads(line)
        assert record["schema_version"] == 2
        assert [message["role"] for message in record["messages"]] == [
            "system",
            "user",
            "assistant",
        ]

    valid, errors = validate_dataset(output)
    assert (valid, errors) == (3, [])


def test_main_exits_2_on_empty_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "dataset.jsonl"

    code = main(
        ["--dry-run", "--output", str(output)],
        session_factory=_session_factory([]),
        record_builder=_fake_builder(),
    )

    assert code == 2
    captured = capsys.readouterr()
    assert "Экспортировано записей: 0" in captured.out
    assert "партий с комментариями" in captured.err
    assert not output.exists()


def test_main_writes_output_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "dataset.jsonl"

    code = main(
        ["--limit", "2", "--output", str(output)],
        session_factory=_session_factory(_rows(1, 2, 3)),
        record_builder=_fake_builder(),
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "Экспортировано записей: 2" in captured.out
    assert f"Файл: {output}" in captured.out
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


# --- CLI-валидация ----------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--limit", "0"],
        ["--limit", "not-a-number"],
        ["--min-words", "-1"],
        ["--level", "master"],
    ],
)
def test_cli_rejects_invalid_args_with_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code == 2


def test_defaults_match_documented_values() -> None:
    args = build_parser().parse_args([])

    assert args.limit == DEFAULT_LIMIT == 500
    assert args.level == "all"
    assert args.min_words == 0
    assert args.dry_run is False
    assert args.seed is None


def test_generation_stats_summary_lists_every_skip_reason() -> None:
    stats = GenerationStats(limit=10)
    stats.record_exported("club")
    stats.record_skipped("no_analysis")
    stats.record_skipped("min_words")

    result = GenerationResult(output=Path("x.jsonl"), dry_run=False, stats=stats)

    lines = result.summary_lines()
    assert "Экспортировано записей: 1" in lines
    assert any("нет анализа ходов" in line for line in lines)
    assert any("--min-words" in line for line in lines)
