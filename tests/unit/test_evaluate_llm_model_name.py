"""Метка версии модели в LLM-eval (итерация 60, подготовка v4).

v3 и v4 оцениваются на **одном** замороженном наборе, поэтому отчёт и JSON
должны различать прогоны: API может вернуть базовое имя модели вместо имени
LoRA-адаптера. Флаг `--model-name` (по умолчанию
`qwen-7b-tokenizer_gguf-v4`) пишет явную метку в заголовок отчёта и в каждую
запись JSON рядом с API-именем `model`.

Все тесты офлайн: БД, Stockfish и LLM не нужны.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_llm import (
    DEFAULT_MODEL_NAME,
    CommentaryRow,
    GameRow,
    _dump_json,
    _parse_args,
    _report,
    main,
)

GAME = GameRow(
    game_id=-1,
    pgn="1. e4 e5",
    white="Stein, Leonid",
    black="Geller, Efim",
    event="Moscow zt",
)

V3 = "qwen-7b-tokenizer_gguf-v3"
V4 = "qwen-7b-tokenizer_gguf-v4"


def _row(model: str = "qwen-7b-tokenizer") -> CommentaryRow:
    return CommentaryRow(
        game=GAME,
        level="club",
        text="Белые захватили центр.",
        model=model,
    )


# --- CLI ---------------------------------------------------------------------


def test_default_model_name_is_v4() -> None:
    assert DEFAULT_MODEL_NAME == V4


def test_parse_args_uses_default_model_name() -> None:
    args = _parse_args(["--games", "1"])

    assert args.model_name == DEFAULT_MODEL_NAME


def test_parse_args_accepts_model_name_override() -> None:
    args = _parse_args(["--model-name", V3])

    assert args.model_name == V3


def test_main_rejects_blank_model_name_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Пустая метка — ошибка ввода до БД/LLM (тест полностью офлайн)."""
    assert main(["--model-name", "   ", "--games", "1"]) == 2

    captured = capsys.readouterr()
    assert "--model-name" in captured.err


# --- отчёт -------------------------------------------------------------------


def test_report_contains_model_name_in_header() -> None:
    report = _report([], [GAME], ["club"], 300.0, model_name=V4)

    assert f"> **Модель:** `{V4}`" in report
    assert "метка eval" in report


def test_report_without_model_name_stays_backward_compatible() -> None:
    report = _report([], [GAME], ["club"], 300.0)

    assert "**Модель:**" not in report


def test_report_distinguishes_v3_and_v4() -> None:
    v3_report = _report([], [GAME], ["club"], 300.0, model_name=V3)
    v4_report = _report([], [GAME], ["club"], 300.0, model_name=V4)

    assert V3 in v3_report
    assert V4 not in v3_report
    assert V4 in v4_report


# --- JSON --------------------------------------------------------------------


def test_dump_json_keeps_api_model_and_adds_model_name(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval_results.json"

    _dump_json([_row()], path, model_name=V4)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["model"] == "qwen-7b-tokenizer"  # имя от API
    assert payload[0]["model_name"] == V4  # метка версии из --model-name


def test_dump_json_without_model_name_is_null(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval_results.json"

    _dump_json([_row()], path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["model_name"] is None


def test_dump_json_writes_model_name_for_every_row(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval_results.json"
    rows = [_row(), _row(model="qwen-7b-tokenizer")]

    _dump_json(rows, path, model_name=V3)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [item["model_name"] for item in payload] == [V3, V3]
