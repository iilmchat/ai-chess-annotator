"""Unit-тесты конвертера внешних PGN-датасетов (T-087).

Проверяем извлечение и очистку комментариев, сборку v2-записи через
``build_fact_package``, причины пропусков, лимит, ``--dry-run`` и
совместимость результата с ``scripts/train_lora.py``. Сеть и БД не нужны:
PGN-строки небольшие и лежат прямо в тестах.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import chess.pgn
import pytest

from app.dataset.export import load_system_prompt
from app.ingestion import parse_pgn
from app.llm.prompts import build_fact_package
from scripts.convert_external_datasets import (
    DEFAULT_INPUT,
    DEFAULT_LIMIT,
    DEFAULT_OUTPUT,
    MAX_ASSISTANT_CHARS,
    ConversionConfig,
    ConversionStats,
    build_parser,
    clean_comment,
    convert_record,
    extract_comments,
    extract_pgn,
    main,
    merge_comments,
    run_conversion,
    truncate_text,
)
from scripts.train_lora import validate_dataset

VALID_PGN = """[Event "External"]
[Site "?"]
[Date "2026.10.31"]
[Round "?"]
[White "Alice"]
[Black "Bob"]
[Result "*"]

1. e4 {A sufficiently long comment about the centre.} e5
2. Nf3 {Another sufficiently long comment about development.} Nc6 *
"""

TAGGED_PGN = """[Event "Tags"]
[White "Alice"]
[Black "Bob"]
[Result "*"]

1. e4 {A sufficiently long comment [%eval +0.35] about the centre.} e5
2. Nf3 {Another comment [%clk 0:03:00] about development and pieces.} Nc6 *
"""

NO_COMMENT_PGN = "1. e4 e5 2. Nf3 Nc6 *"
SHORT_COMMENT_PGN = "1. e4 {short} e5 *"
VARIATION_PGN = (
    "1. e4 {Main comment one is long enough.} "
    "(1. d4 {Variation comment is long enough.}) "
    "e5 {Main comment two is long enough.} *"
)
LONG_COMMENT = "word " * 600
LONG_COMMENT_PGN = f"1. e4 {{ {LONG_COMMENT} }} e5 *"


def _game(pgn: str) -> chess.pgn.Game:
    game = chess.pgn.read_game(io.StringIO(pgn))
    assert game is not None
    return game


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _valid_lines(count: int) -> list[str]:
    return [
        json.dumps(
            {"id": f"game_{index}", "origin": "test", "text": VALID_PGN},
            ensure_ascii=False,
        )
        for index in range(count)
    ]


# --- извлечение и очистка ---------------------------------------------------


def test_extract_pgn_reads_text_field() -> None:
    assert extract_pgn({"text": "  1. e4 e5  "}) == "1. e4 e5"


@pytest.mark.parametrize(
    "raw",
    [{}, {"text": ""}, {"text": "   "}, {"text": 42}, {"text": None}],
)
def test_extract_pgn_returns_none_without_text(raw: dict[str, object]) -> None:
    assert extract_pgn(raw) is None


def test_clean_comment_strips_eval_and_clock_tags() -> None:
    raw = " Good move [%eval +0.35] [%clk 0:03:00]  in the centre. "

    assert clean_comment(raw) == "Good move in the centre."


def test_clean_comment_collapses_whitespace() -> None:
    assert clean_comment("  spaced\n\t comment  ") == "spaced comment"


def test_extract_comments_reads_mainline_only() -> None:
    comments = extract_comments(_game(VARIATION_PGN))

    assert len(comments) == 2
    assert comments[0] == "Main comment one is long enough."
    assert comments[1] == "Main comment two is long enough."
    assert not any("Variation" in comment for comment in comments)


def test_extract_comments_drops_empty_tag_only_comments() -> None:
    pgn = "1. e4 {[%eval 0.2][%clk 0:01:00]} e5 *"

    assert extract_comments(_game(pgn)) == []


def test_merge_comments_joins_with_single_spaces() -> None:
    assert (
        merge_comments(["one  two", "three\nfour"])
        == "one two three four"
    )


# --- обрезка ----------------------------------------------------------------


def test_truncate_text_keeps_short_text() -> None:
    assert truncate_text("short text") == "short text"


def test_truncate_text_cuts_at_word_boundary() -> None:
    result = truncate_text(LONG_COMMENT, limit=50)

    assert len(result) <= 50
    assert not result.endswith(" ")
    assert all(part == "word" for part in result.split())


def test_truncate_text_hard_cuts_single_long_word() -> None:
    result = truncate_text("a" * 100, limit=10)

    assert result == "a" * 10


def test_truncate_text_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        truncate_text("text", limit=0)


# --- одна запись ------------------------------------------------------------


def test_convert_record_builds_schema_v2_contract() -> None:
    raw = {"id": "game_1", "origin": "test", "text": VALID_PGN}

    conversion = convert_record(raw, line_number=7)

    assert conversion.skip_reason is None
    assert conversion.record is not None
    record = conversion.record
    assert record["schema_version"] == 2
    assert [message["role"] for message in record["messages"]] == [
        "system",
        "user",
        "assistant",
    ]
    messages = record["messages"]
    assert messages[0]["content"] == load_system_prompt("club")
    assert messages[1]["content"] == build_fact_package(
        parse_pgn(VALID_PGN), level="club"
    )
    assert "# Партия" in messages[1]["content"]
    assert "# Ходы партии (SAN)" in messages[1]["content"]
    assert "1. e4 e5 2. Nf3 Nc6" in messages[1]["content"]
    assert "Анализ движка недоступен" in messages[1]["content"]
    assert messages[2]["content"] == (
        "A sufficiently long comment about the centre. "
        "Another sufficiently long comment about development."
    )
    assert conversion.assistant_chars == len(messages[2]["content"])

    metadata = record["metadata"]
    assert metadata["id"] == 7
    assert metadata["level"] == "club"
    assert metadata["game_id"] is None
    assert metadata["created_at"] is None
    assert metadata["fact_sheet_chars"] == len(messages[1]["content"])
    assert metadata["assistant_chars"] == len(messages[2]["content"])
    assert metadata["comment_count"] == 2
    assert metadata["source"] == "pkloats/bishop-chess-dataset"
    assert metadata["source_id"] == "game_1"
    assert metadata["origin"] == "test"


def test_convert_record_strips_tags_from_assistant() -> None:
    conversion = convert_record({"text": TAGGED_PGN}, line_number=1)

    assert conversion.record is not None
    assistant = conversion.record["messages"][2]["content"]
    assert "[%eval" not in assistant
    assert "[%clk" not in assistant
    assert "A sufficiently long comment about the centre." in assistant
    assert "Another comment about development and pieces." in assistant


def test_convert_record_caps_assistant_length() -> None:
    conversion = convert_record({"text": LONG_COMMENT_PGN}, line_number=1)

    assert conversion.record is not None
    assistant = conversion.record["messages"][2]["content"]
    assert len(assistant) <= MAX_ASSISTANT_CHARS
    assert len(assistant) > MAX_ASSISTANT_CHARS - 20
    assert not assistant.endswith(" ")
    assert conversion.assistant_chars == len(assistant)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({}, "empty_text"),
        ({"text": ""}, "empty_text"),
        ({"text": "not a chess game"}, "invalid_pgn"),
        ({"text": "1. e4 e5 2. Ke3"}, "invalid_pgn"),
        ({"text": NO_COMMENT_PGN}, "no_comments"),
        ({"text": SHORT_COMMENT_PGN}, "short_comments"),
        ({"text": "1. e4 {[%eval 0.1]} e5 *"}, "no_comments"),
    ],
)
def test_convert_record_reports_skip_reason(
    raw: dict[str, object], reason: str
) -> None:
    conversion = convert_record(raw, line_number=1)

    assert conversion.record is None
    assert conversion.skip_reason == reason


# --- прогон по файлу --------------------------------------------------------


def test_run_conversion_counts_skips_and_writes_valid_records(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    lines = [
        "",
        "not json",
        json.dumps({"text": ""}),
        json.dumps({"text": "not a chess game"}),
        json.dumps({"text": NO_COMMENT_PGN}),
        json.dumps({"text": SHORT_COMMENT_PGN}),
        *_valid_lines(2),
    ]
    _write_jsonl(source, lines)

    result = run_conversion(
        ConversionConfig(input_path=source, output_path=output, limit=10)
    )

    assert result.stats.total == 7
    assert result.stats.converted == 2
    assert result.stats.skipped_total == 5
    assert result.stats.skipped == {
        "invalid_json": 1,
        "empty_text": 1,
        "invalid_pgn": 1,
        "no_comments": 1,
        "short_comments": 1,
    }
    assert result.stats.average_assistant_chars > 0
    assert output.is_file()
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2
    count, errors = validate_dataset(output)
    assert (count, errors) == (2, [])


def test_run_conversion_respects_limit(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, _valid_lines(3))

    result = run_conversion(
        ConversionConfig(input_path=source, output_path=output, limit=2)
    )

    assert result.stats.total == 2
    assert result.stats.converted == 2
    assert len(output.read_text(encoding="utf-8").splitlines()) == 2


def test_run_conversion_dry_run_does_not_write(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, _valid_lines(1))

    result = run_conversion(
        ConversionConfig(
            input_path=source, output_path=output, limit=10, dry_run=True
        )
    )

    assert result.stats.converted == 1
    assert not output.exists()
    assert any("--dry-run" in line for line in result.summary_lines())


def test_run_conversion_keeps_old_file_when_nothing_converted(
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    output.write_text("old data\n", encoding="utf-8")
    _write_jsonl(source, [json.dumps({"text": "not a chess game"})])

    result = run_conversion(
        ConversionConfig(input_path=source, output_path=output, limit=10)
    )

    assert result.stats.converted == 0
    assert output.read_text(encoding="utf-8") == "old data\n"


def test_run_conversion_missing_input(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="не найден"):
        run_conversion(
            ConversionConfig(
                input_path=tmp_path / "missing.jsonl",
                output_path=tmp_path / "out.jsonl",
                limit=1,
            )
        )


def test_run_conversion_rejects_zero_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        run_conversion(
            ConversionConfig(
                input_path=tmp_path / "input.jsonl",
                output_path=tmp_path / "out.jsonl",
                limit=0,
            )
        )


# --- статистика и CLI -------------------------------------------------------


def test_conversion_stats_average_and_max() -> None:
    stats = ConversionStats(limit=10)
    stats.record_read()
    stats.record_converted(100)
    stats.record_converted(300)
    stats.record_skipped("invalid_pgn")
    stats.record_skipped("no_comments")

    assert stats.total == 1
    assert stats.converted == 2
    assert stats.skipped_total == 2
    assert stats.average_assistant_chars == 200
    assert stats.assistant_max == 300


def test_summary_lines_are_human_readable(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, _valid_lines(1))
    result = run_conversion(
        ConversionConfig(input_path=source, output_path=output, limit=10)
    )

    text = "\n".join(result.summary_lines())

    assert "schema_version=2" in text
    assert "Прочитано записей: 1" in text
    assert "Сконвертировано записей: 1" in text
    assert "Пропущено записей: 0" in text
    assert "Средняя длина assistant:" in text
    assert f"Файл: {output}" in text


def test_cli_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.input == DEFAULT_INPUT
    assert args.output == DEFAULT_OUTPUT
    assert args.limit == DEFAULT_LIMIT
    assert args.dry_run is False


def test_cli_rejects_zero_limit() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--limit", "0"])

    assert exc_info.value.code == 2


def test_main_returns_2_for_missing_input(tmp_path: Path) -> None:
    code = main(
        [
            "--input",
            str(tmp_path / "missing.jsonl"),
            "--output",
            str(tmp_path / "out.jsonl"),
            "--dry-run",
        ]
    )

    assert code == 2


def test_main_converts_file(tmp_path: Path) -> None:
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, _valid_lines(1))

    code = main(
        [
            "--input",
            str(source),
            "--output",
            str(output),
            "--limit",
            "1",
        ]
    )

    assert code == 0
    assert output.is_file()
    assert validate_dataset(output) == (1, [])
