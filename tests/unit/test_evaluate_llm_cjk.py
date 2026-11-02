"""Тесты CJK-метрики LLM-eval (KI-060, итерация 50).

Проверяются: подсчёт доли иероглифов, сводка по комментариям, вывод в
консольный отчёт `LLM_EVAL.md`, JSON и совместимость с именем v2-модели
`qwen-7b-tokenizer` (никаких веток по имени модели в скрипте нет).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_llm import (
    CommentaryRow,
    GameRow,
    _cjk_summary,
    _dump_json,
    _report,
    cjk_stats,
)

GAME = GameRow(
    game_id=1,
    pgn="1. e4 e5",
    white="White",
    black="Black",
    event="Test",
)

CLEAN = "Партия прошла в напряжённой борьбе, белые захватили центр."
DRIFTED = "Партия была интересной. Конь идет на 马e4."


def _row(text: str, level: str = "club", model: str = "qwen-7b-tokenizer") -> CommentaryRow:
    return CommentaryRow(game=GAME, level=level, text=text, model=model)


# --- подсчёт ----------------------------------------------------------------


def test_cjk_stats_clean_text() -> None:
    assert cjk_stats(CLEAN) == (0, 0.0)


def test_cjk_stats_empty_text() -> None:
    assert cjk_stats("") == (0, 0.0)


def test_cjk_stats_single_character_share() -> None:
    chars, share = cjk_stats(DRIFTED)

    assert chars == 1
    assert 0.0 < share < 0.1


def test_cjk_stats_all_cjk() -> None:
    chars, share = cjk_stats("马马马")

    assert chars == 3
    assert share == pytest.approx(1.0)


# --- сводка -----------------------------------------------------------------


def test_cjk_summary_aggregates_over_non_space_chars() -> None:
    rows = [_row(CLEAN), _row(DRIFTED)]

    summary = _cjk_summary(rows)

    total = sum(1 for row in rows for ch in row.text if not ch.isspace())
    assert summary.cjk_chars == 1
    assert summary.comments_with_cjk == 1
    assert summary.evaluated == 2
    assert summary.share == pytest.approx(1 / total)


def test_cjk_summary_ignores_error_rows() -> None:
    rows = [
        _row(""),
        CommentaryRow(game=GAME, level="club", error="502"),
        _row(DRIFTED),
    ]

    summary = _cjk_summary(rows)

    assert summary.evaluated == 1
    assert summary.comments_with_cjk == 1


def test_cjk_summary_no_drift() -> None:
    summary = _cjk_summary([_row(CLEAN)])

    assert summary.cjk_chars == 0
    assert summary.comments_with_cjk == 0
    assert summary.evaluated == 1
    assert summary.share == 0.0


# --- отчёт LLM_EVAL.md -------------------------------------------------------


def test_report_contains_cjk_metric_and_columns() -> None:
    rows = [_row(CLEAN), _row(DRIFTED, level="gm")]

    report = _report(rows, [GAME], ["club", "gm"], 300.0)

    assert "**Доля CJK-символов (KI-060):" in report
    assert "комментариев с иероглифами: 1/2" in report
    assert "| CJK |" in report
    assert "| `club` |" in report
    assert "| `gm` |" in report
    assert "CJK-дрейф (KI-060)" in report
    assert "1 из 2 комментариев содержат иероглифы" in report


def test_report_says_no_drift_for_clean_run() -> None:
    report = _report([_row(CLEAN)], [GAME], ["club"], 300.0)

    assert "CJK-дрейф (KI-060):** иероглифов не обнаружено" in report
    assert "0/1 комментариев" in report


def test_report_mentions_v2_model_agnostic_metric() -> None:
    report = _report([_row(CLEAN)], [GAME], ["club"], 300.0)

    assert "qwen-7b-tokenizer" in report


# --- JSON -------------------------------------------------------------------


def test_json_dump_contains_cjk_fields(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval.json"

    _dump_json([_row(CLEAN), _row(DRIFTED)], path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [item["cjk_chars"] for item in payload] == [0, 1]
    assert payload[0]["cjk_share"] == 0.0
    assert payload[1]["cjk_share"] > 0
    assert payload[0]["model"] == "qwen-7b-tokenizer"


def test_json_dump_keeps_v2_model_name_without_special_casing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "llm_eval.json"

    _dump_json([_row(CLEAN, model="qwen-7b-tokenizer")], path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload[0]["model"] == "qwen-7b-tokenizer"
