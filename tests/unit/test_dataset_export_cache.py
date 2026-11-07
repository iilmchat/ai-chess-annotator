"""Кэш `load_analyses` при экспорте датасета (KI-044, итерация 63).

Несколько правок одной партии не должны заново читать `moves`/`motifs`:
кэш живёт на время одной выгрузки и ключуется по `game_id`. Тесты
офлайновые — БД не нужна, `load_analyses` и `build_fact_sheet` подменены.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from app.dataset.export import ExportStats
from app.dataset.records import ExportFilters, iter_export_records
from app.db.models import Commentary, Edit, Game, Level
from scripts.export_dataset import _export_v2


class _FakeResult:
    """Минимальный результат `AsyncSession.execute`: только `.all()`."""

    def __init__(self, rows: list[tuple[Edit, Commentary, Game]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Edit, Commentary, Game]]:
        return self._rows


class _FakeSession:
    """Сессия, отдающая заранее заданные батчи строк."""

    def __init__(self, batches: list[list[tuple[Edit, Commentary, Game]]]) -> None:
        self._batches = list(batches)
        self.execute_calls = 0

    async def execute(self, _statement: object) -> _FakeResult:
        self.execute_calls += 1
        rows = self._batches.pop(0) if self._batches else []
        return _FakeResult(rows)


def _rows(
    game_id: int,
    edits: int,
    *,
    level: Level = Level.club,
) -> list[tuple[Edit, Commentary, Game]]:
    """Партия + один комментарий + N правок этого комментария."""
    game = Game(id=game_id, pgn="1. e4 e5 2. Nf3 Nc6", eco_code="C50")
    commentary = Commentary(
        id=game_id * 10,
        game_id=game_id,
        level=level,
        text="исходный комментарий",
        model="test-model",
    )
    created_at = datetime(2026, 11, 8, tzinfo=UTC)
    return [
        (
            Edit(
                id=game_id * 100 + index,
                commentary_id=commentary.id,
                original_text="исходный комментарий",
                edited_text=f"правка {index}",
                created_at=created_at,
            ),
            commentary,
            game,
        )
        for index in range(1, edits + 1)
    ]


def _patch_analyses(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[int],
    *,
    result: list[object] | None = None,
) -> None:
    """Подменить `load_analyses` и тяжёлую сборку пакета фактов."""

    async def fake_load(
        _session: object, game: Game, _parsed: object
    ) -> list[object]:
        calls.append(game.id)
        return result if result is not None else [object()]

    monkeypatch.setattr("app.dataset.records.load_analyses", fake_load)
    monkeypatch.setattr(
        "app.dataset.records.build_fact_sheet",
        lambda *_args: "пакет фактов",
    )
    monkeypatch.setattr(
        "scripts.export_dataset.build_fact_sheet",
        lambda *_args: "пакет фактов",
    )
    monkeypatch.setattr(
        "app.dataset.export.load_system_prompt",
        lambda _level: "system",
    )


async def test_three_edits_of_one_game_call_load_analyses_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rows(game_id=42, edits=3)
    session = _FakeSession([rows, []])
    calls: list[int] = []
    _patch_analyses(monkeypatch, calls)

    records = [
        record
        async for record in iter_export_records(
            cast(Any, session), ExportFilters()
        )
    ]

    assert len(records) == 3
    assert calls == [42]
    assert session.execute_calls == 2  # один батч + пустой терминальный


async def test_export_v2_three_edits_of_one_game_call_load_analyses_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Путь `scripts/export_dataset.py`, на который указывал KI-044."""
    rows = _rows(game_id=7, edits=3)
    session = _FakeSession([rows, []])
    calls: list[int] = []
    _patch_analyses(monkeypatch, calls)
    output = tmp_path / "dataset_v2.jsonl"
    stats = ExportStats()

    await _export_v2(
        cast(Any, session),
        output,
        limit=None,
        level=None,
        min_id=0,
        stats=stats,
    )

    assert stats.exported == 3
    assert calls == [7]
    assert len(output.read_text(encoding="utf-8").splitlines()) == 3


async def test_cache_is_keyed_by_game_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _rows(game_id=1, edits=2) + _rows(game_id=2, edits=2)
    session = _FakeSession([rows, []])
    calls: list[int] = []
    _patch_analyses(monkeypatch, calls)

    records = [
        record
        async for record in iter_export_records(
            cast(Any, session), ExportFilters()
        )
    ]

    assert len(records) == 4
    assert calls == [1, 2]


async def test_empty_analysis_is_cached_for_all_edits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Партия без анализа: повторно в БД не ходим, все правки пропускаются."""
    rows = _rows(game_id=5, edits=3)
    session = _FakeSession([rows, []])
    calls: list[int] = []
    _patch_analyses(monkeypatch, calls, result=[])

    records = [
        record
        async for record in iter_export_records(
            cast(Any, session), ExportFilters()
        )
    ]

    assert records == []
    assert calls == [5]
