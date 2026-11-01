"""Unit-тесты сборщика золотого датасета из Lichess PGN (T-092)."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import chess.pgn
import pytest

from app.ingestion import parse_pgn
from scripts.build_gold_dataset import (
    MOCK_RESPONSE,
    EvalPoint,
    GoldConfig,
    analyses_from_evals,
    build_parser,
    build_record,
    export_game_pgn,
    extract_eval_points,
    main,
    parse_eval_comment,
    run_build,
)
from scripts.train_lora import validate_dataset

EVAL_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/game1"]
[White "White"]
[Black "Black"]
[Result "1-0"]
[WhiteElo "1900"]
[BlackElo "2000"]

1. e4 { [%eval 0.20] } e5 { [%eval 0.10] }
2. Nf3 { [%eval -1.50] } Nc6 { [%eval -1.40] } 1-0
"""

NO_EVAL_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/noeval"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 e5 2. Nf3 Nc6 1-0
"""

MATE_PGN = """[Event "Rated Blitz game"]
[Site "https://lichess.org/mate"]
[White "White"]
[Black "Black"]
[Result "1-0"]

1. e4 { [%eval #3] } e5 { [%eval #-2] } 1-0
"""


def _write_pgn(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _game(pgn: str) -> Any:
    games = iter_games_from_text(pgn)
    assert len(games) == 1
    return games[0]


def iter_games_from_text(pgn: str) -> list[Any]:
    stream = io.StringIO(pgn)
    games = []
    while True:
        game = chess.pgn.read_game(stream)
        if game is None:
            break
        games.append(game)
    return games


# --- оценки -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("comment", "cp", "mate"),
    [
        ("[%eval 0.23]", 23, None),
        ("[%eval -1.09]", -109, None),
        ("[%eval +0.00]", 0, None),
        ("[%eval #3]", 9700, 3),
        ("[%eval #-3]", -9700, -3),
    ],
)
def test_parse_eval_comment(comment: str, cp: int, mate: int | None) -> None:
    point = parse_eval_comment(comment)

    assert point == EvalPoint(cp=cp, mate=mate)


@pytest.mark.parametrize(
    "comment",
    ["", "обычный комментарий", "[%eval abc]", "[%eval #0]"],
)
def test_parse_eval_comment_returns_none(comment: str) -> None:
    assert parse_eval_comment(comment) is None


def test_extract_eval_points_one_per_ply() -> None:
    game = _game(EVAL_PGN)

    points = extract_eval_points(game)

    assert len(points) == 4
    assert points[0] == EvalPoint(cp=20)
    assert points[2] == EvalPoint(cp=-150)


def test_analyses_from_evals_compute_delta_and_no_best_move() -> None:
    parsed = parse_pgn(EVAL_PGN)
    points = extract_eval_points(_game(EVAL_PGN))

    analyses = analyses_from_evals(parsed, points)

    assert len(analyses) == 4
    assert analyses[0].eval_before == 0
    assert analyses[0].eval_after == 20
    assert analyses[0].best_move_known is False
    assert analyses[2].played_move_san == "Nf3"
    assert analyses[2].eval_before == 10
    assert analyses[2].eval_after == -150
    assert analyses[2].delta_cp == 160
    assert analyses[2].classification == "mistake"
    assert analyses[2].best_move_san == "Nf3"
    assert analyses[2].best_move_known is False


def test_analyses_from_evals_mate() -> None:
    parsed = parse_pgn(MATE_PGN)
    points = extract_eval_points(_game(MATE_PGN))

    analyses = analyses_from_evals(parsed, points)

    assert analyses[0].mate_after == 3
    assert analyses[0].eval_after == 9700
    assert analyses[1].mate_after == -2


def test_analyses_from_evals_rejects_mismatch() -> None:
    parsed = parse_pgn(EVAL_PGN)

    with pytest.raises(ValueError, match="полуходов"):
        analyses_from_evals(parsed, [])


# --- v2-запись --------------------------------------------------------------


def test_build_record_has_v2_contract() -> None:
    parsed = parse_pgn(EVAL_PGN)
    game = _game(EVAL_PGN)
    analyses = analyses_from_evals(parsed, extract_eval_points(game))

    record = build_record(
        game=game,
        parsed=parsed,
        fact_sheet="FACT",
        text="ответ",
        system_prompt="system",
        level="club",
        model="mock",
        source_id="file.pgn:1",
        eval_points=len(analyses),
    )

    assert record["schema_version"] == 2
    assert [message["role"] for message in record["messages"]] == [
        "system",
        "user",
        "assistant",
    ]
    assert record["metadata"]["eval_points"] == 4
    assert record["metadata"]["source"] == "lichess"
    assert record["metadata"]["id"] == "file.pgn:1"


def test_export_game_pgn_keeps_comments() -> None:
    pgn = export_game_pgn(_game(EVAL_PGN))

    assert "[%eval 0.20]" in pgn
    assert "[WhiteElo \"1900\"]" in pgn


# --- сборка -----------------------------------------------------------------


async def test_run_build_mock_writes_v2_dataset(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    output = tmp_path / "gold.jsonl"

    result = await run_build(
        GoldConfig(
            input_dir=input_dir,
            output=output,
            limit=10,
            provider="mock",
        )
    )

    assert result.stats.converted == 1
    assert result.stats.eval_points == 4
    assert output.is_file()
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["schema_version"] == 2
    assert record["messages"][2]["content"] == MOCK_RESPONSE
    assert record["messages"][1]["content"].startswith("# Партия")
    assert "Лучший ход движка неизвестен" in record["messages"][1]["content"]
    assert "Потеря относительно лучшей линии: 160 cp" in record["messages"][1]["content"]
    assert validate_dataset(output) == (1, [])


async def test_run_build_skips_no_evals(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "noeval.pgn", NO_EVAL_PGN)
    output = tmp_path / "gold.jsonl"

    result = await run_build(
        GoldConfig(input_dir=input_dir, output=output, provider="mock")
    )

    assert result.stats.converted == 0
    assert result.stats.skipped["no_evals"] == 1
    assert not output.exists()


async def test_run_build_skips_invalid_pgn(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "bad.pgn", "not a pgn")
    output = tmp_path / "gold.jsonl"

    result = await run_build(
        GoldConfig(input_dir=input_dir, output=output, provider="mock")
    )

    assert result.stats.converted == 0
    assert result.stats.skipped["invalid_pgn"] == 1


async def test_run_build_respects_limit(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(
        input_dir / "two.pgn", EVAL_PGN + "\n\n" + EVAL_PGN
    )

    result = await run_build(
        GoldConfig(
            input_dir=input_dir,
            output=tmp_path / "out.jsonl",
            limit=1,
            provider="mock",
        )
    )

    assert result.stats.converted == 1


async def test_run_build_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="каталог"):
        await run_build(
            GoldConfig(
                input_dir=tmp_path / "missing",
                output=tmp_path / "out.jsonl",
                provider="mock",
            )
        )


async def test_run_build_empty_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "empty"
    input_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="нет"):
        await run_build(
            GoldConfig(
                input_dir=input_dir,
                output=tmp_path / "out.jsonl",
                provider="mock",
            )
        )


class _FakeLLM:
    """Клиент с заранее заданными ответами (проверка retry/валидации)."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls = 0
        self.model = "fake"
        self.level: str | None = None

    def set_level(self, level: str | None) -> None:
        self.level = level

    async def complete(self, prompt: str, system_prompt: str | None = None) -> Any:
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return SimpleNamespace(text=answer)

    async def aclose(self) -> None:
        return None


async def test_local_llm_retries_illegal_answer(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    client = _FakeLLM(["Qxf7", "Партия прошла спокойно."])

    result = await run_build(
        GoldConfig(
            input_dir=input_dir,
            output=tmp_path / "out.jsonl",
            provider="local",
            max_retries=1,
        ),
        llm_client=client,  # type: ignore[arg-type]
    )

    assert result.stats.converted == 1
    assert client.calls == 2


async def test_local_llm_invalid_is_skipped(tmp_path: Path) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    client = _FakeLLM(["Qxf7"])

    result = await run_build(
        GoldConfig(
            input_dir=input_dir,
            output=tmp_path / "out.jsonl",
            provider="local",
            max_retries=1,
        ),
        llm_client=client,  # type: ignore[arg-type]
    )

    assert result.stats.converted == 0
    assert result.stats.skipped["invalid_llm"] == 1


# --- CLI --------------------------------------------------------------------


def test_parser_defaults() -> None:
    args = build_parser().parse_args([])

    assert args.limit == 1000
    assert args.level == "club"
    assert args.provider == "local"
    assert args.max_retries == 1


def test_parser_rejects_unknown_provider() -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["--provider", "openai"])

    assert exc_info.value.code == 2


def test_main_mock_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    input_dir = tmp_path / "pgns"
    input_dir.mkdir()
    _write_pgn(input_dir / "one.pgn", EVAL_PGN)
    output = tmp_path / "gold.jsonl"

    code = main(
        [
            "--input-dir",
            str(input_dir),
            "--output",
            str(output),
            "--provider",
            "mock",
            "--limit",
            "1",
        ]
    )

    assert code == 0
    assert output.is_file()
    assert "Записей: 1" in capsys.readouterr().out


def test_main_returns_2_for_missing_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--input-dir",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "out.jsonl"),
            "--provider",
            "mock",
        ]
    )

    assert code == 2
    assert "Ошибка" in capsys.readouterr().err
