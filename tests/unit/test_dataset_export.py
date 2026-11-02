"""Формат JSONL для LoRA (T-104/KI-039): system → user → assistant.

v2: `user` — сериализованный пакет фактов, `assistant` — правка редактора.
v1 (legacy): `user` — `original_text` (rewrite-семантика).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.engine.models import MoveAnalysis
from app.errors import InvalidLevelError
from app.ingestion import parse_pgn
from scripts.export_dataset import (
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SUPPORTED_SCHEMA_VERSIONS,
    ExportStats,
    _parse_args,
    build_fact_sheet,
    load_system_prompt,
    record_from_edit,
)

FACT_PACKAGE = (
    "# Партия\nБелые: A\nЧёрные: B\n\n"
    "# Ходы партии (SAN)\n1. e4 e5\n\n"
    "# Ключевые моменты (до 3, по убыванию потери)\n"
    "## 1. Ход 1 белых: e4\n- Сыграно: e4 (белые)"
)

SHORT_PGN = "1. e4 e5 2. Nf3 Nc6"


def _edit() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        commentary_id=3,
        editor_id=1,
        original_text="было",
        edited_text="стало",
        created_at=datetime(2026, 10, 29, 1, 2, 3, tzinfo=UTC),
    )


def _commentary(
    level: str = "club", *, game_id: int = 42
) -> SimpleNamespace:
    return SimpleNamespace(
        level=SimpleNamespace(value=level), model="qwen", game_id=game_id
    )


def _analyses(pgn: str = SHORT_PGN) -> tuple[Any, list[MoveAnalysis]]:
    """Партия + минимальный список MoveAnalysis, достаточный для пакета."""
    parsed = parse_pgn(pgn)
    uci = ("e2e4", "e7e5", "g1f3", "b8c6")
    analyses = [
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
    return parsed, analyses


# --- system-промпт ----------------------------------------------------------


def test_system_prompt_is_loaded_from_prompt_file() -> None:
    text = load_system_prompt("club")

    assert "шахматный комментатор" in text.lower()
    assert "ЗАПРЕЩЕНО" in text


def test_unknown_level_falls_back_to_club_prompt() -> None:
    assert load_system_prompt("master") == load_system_prompt("club")


# --- v2: user = пакет фактов ------------------------------------------------


def test_record_v2_has_system_fact_package_and_edited_text() -> None:
    record = record_from_edit(
        _edit(), _commentary(), fact_package=FACT_PACKAGE
    )

    messages = record["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
    ]
    assert messages[0]["content"] == load_system_prompt("club")
    assert messages[1] == {"role": "user", "content": FACT_PACKAGE}
    assert messages[2] == {"role": "assistant", "content": "стало"}


def test_record_v2_metadata_has_required_keys() -> None:
    record = record_from_edit(
        _edit(), _commentary(), fact_package=FACT_PACKAGE
    )

    assert record["schema_version"] == SCHEMA_VERSION == 2
    metadata = record["metadata"]
    assert metadata == {
        "id": 7,
        "commentary_id": 3,
        "game_id": 42,
        "editor_id": 1,
        "level": "club",
        "model": "qwen",
        "created_at": "2026-10-29T01:02:03+00:00",
        "fact_sheet_chars": len(FACT_PACKAGE),
    }
    # В v2 плоских метаданных на верхнем уровне нет — они в metadata.
    assert "id" not in record
    assert "level" not in record


def test_record_v2_requires_fact_package() -> None:
    with pytest.raises(ValueError, match="fact_package"):
        record_from_edit(_edit(), _commentary())


def test_record_v2_accepts_system_prompt_override() -> None:
    record = record_from_edit(
        _edit(),
        _commentary(),
        fact_package=FACT_PACKAGE,
        system_prompt="кастомный system",
    )

    assert record["messages"][0] == {
        "role": "system",
        "content": "кастомный system",
    }


def test_record_v2_uses_level_prompt() -> None:
    club = record_from_edit(
        _edit(), _commentary("club"), fact_package=FACT_PACKAGE
    )
    gm = record_from_edit(
        _edit(), _commentary("gm"), fact_package=FACT_PACKAGE
    )

    assert gm["messages"][0]["content"] == load_system_prompt("gm")
    assert gm["messages"][0]["content"] != club["messages"][0]["content"]
    assert gm["metadata"]["level"] == "gm"


def test_unknown_schema_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        record_from_edit(
            _edit(), _commentary(), schema_version=3, fact_package=FACT_PACKAGE
        )


# --- v1: legacy rewrite -----------------------------------------------------


def test_record_v1_keeps_original_text_as_user() -> None:
    record = record_from_edit(
        _edit(), _commentary(), schema_version=LEGACY_SCHEMA_VERSION
    )

    messages = record["messages"]
    assert messages[1] == {"role": "user", "content": "было"}
    assert messages[2] == {"role": "assistant", "content": "стало"}
    assert record["schema_version"] == 1
    assert record["id"] == 7
    assert record["commentary_id"] == 3
    assert record["editor_id"] == 1
    assert record["level"] == "club"
    assert record["model"] == "qwen"
    assert record["created_at"].startswith("2026-10-29T01:02:03")
    # В v1 metadata плоская, а факт-пакета нет.
    assert "metadata" not in record


def test_supported_schema_versions_are_one_and_two() -> None:
    assert SUPPORTED_SCHEMA_VERSIONS == (1, 2)
    assert SCHEMA_VERSION == 2


# --- пакет фактов: реальный ParsedGame/MoveAnalysis -------------------------


def test_build_fact_sheet_from_real_parsed_game_and_analyses() -> None:
    parsed, analyses = _analyses()

    sheet = build_fact_sheet(parsed, analyses, "club")

    assert "# Партия" in sheet
    assert "# Ходы партии (SAN)" in sheet
    assert "1. e4 e5 2. Nf3 Nc6" in sheet
    assert "# Ключевые моменты" in sheet
    assert "Уровень объяснения: club" in sheet
    assert "Не добавляй ходы и оценки" in sheet


def test_build_fact_sheet_rejects_empty_analyses() -> None:
    parsed, _ = _analyses()

    with pytest.raises(ValueError, match="нет анализа ходов"):
        build_fact_sheet(parsed, [], "club")


def test_build_fact_sheet_rejects_unknown_level() -> None:
    parsed, analyses = _analyses()

    with pytest.raises(InvalidLevelError):
        build_fact_sheet(parsed, analyses, "master")


def test_record_v2_from_real_fact_sheet_has_consistent_chars() -> None:
    parsed, analyses = _analyses()
    sheet = build_fact_sheet(parsed, analyses, "club")

    record = record_from_edit(_edit(), _commentary(), fact_package=sheet)

    assert record["messages"][1]["content"] == sheet
    assert record["metadata"]["fact_sheet_chars"] == len(sheet)


# --- статистика пропусков ---------------------------------------------------


def test_export_stats_counts_exported_and_skipped() -> None:
    stats = ExportStats()

    stats.record_exported("club", "x" * 100)
    stats.record_exported("club", "y" * 300)
    stats.record_skipped("no_analysis")
    stats.record_skipped("invalid_pgn")
    stats.record_skipped("invalid_pgn")

    assert stats.exported == 2
    assert stats.skipped_total == 3
    assert stats.skipped["invalid_pgn"] == 2
    assert stats.levels == {"club": 2}
    assert stats.fact_sheet_chars == 400


def test_export_stats_summary_lines_are_human_readable() -> None:
    stats = ExportStats()
    stats.record_exported("club", "x" * 100)
    stats.record_skipped("no_analysis")

    text = "\n".join(stats.summary_lines())

    assert "schema_version=2" in text
    assert "Экспортировано записей: 1" in text
    assert "Пропущено записей: 1" in text
    assert "нет анализа ходов" in text
    assert "club=1" in text
    assert "в среднем 100 символов" in text


def test_export_stats_empty_dataset_summary() -> None:
    stats = ExportStats()

    text = "\n".join(stats.summary_lines())

    assert "Экспортировано записей: 0" in text
    assert "Пропущено записей: 0" in text
    assert "нет данных" in text


# --- CLI --------------------------------------------------------------------


def test_cli_schema_version_defaults_to_v2() -> None:
    args = _parse_args([])

    assert args.schema_version == SCHEMA_VERSION


def test_cli_accepts_legacy_schema_version() -> None:
    args = _parse_args(["--schema-version", "1"])

    assert args.schema_version == LEGACY_SCHEMA_VERSION


def test_cli_rejects_unknown_schema_version() -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_args(["--schema-version", "3"])

    assert exc_info.value.code == 2
