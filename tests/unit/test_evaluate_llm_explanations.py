"""Тесты проверки объяснений в LLM-eval (итерация 38).

Проверяем чистую функцию `has_explanation`: RU/EN-маркеры, регистр,
варианты пробелов, пустой текст и текст-констатацию без объяснения.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluate_llm import (
    EXPLANATION_MARKERS,
    CommentaryRow,
    GameRow,
    _dump_json,
    _report,
    has_explanation,
)

RU_EXPLANATIONS = [
    "У чёрных слабое поле f6, потому что пешка g7 ушла вперёд.",
    "Белые выиграли темп, так как соперник потратил ход на профилактику.",
    "Слон уходит с диагонали, поэтому король остаётся без защиты.",
]

EN_EXPLANATIONS = [
    "Black's f6 square is weak because the g7 pawn has advanced.",
    "White is better since the bishop controls the long diagonal.",
]

DRY_FACT = "Слабое поле f6. Белые играют Nf3, оценка +0.3."

GAME = GameRow(
    game_id=1,
    pgn="1. e4 e5",
    white="White",
    black="Black",
    event="Test",
)


def _rows() -> list[CommentaryRow]:
    return [
        CommentaryRow(
            game=GAME,
            level="club",
            text="Слон ушёл, потому что король остался без защиты.",
            has_explanation=True,
        ),
        CommentaryRow(
            game=GAME,
            level="gm",
            text="Black is better because the bishop controls the centre.",
            has_explanation=True,
        ),
    ]


@pytest.mark.parametrize("text", RU_EXPLANATIONS)
def test_russian_explanations_are_detected(text: str) -> None:
    assert has_explanation(text) is True


@pytest.mark.parametrize("text", EN_EXPLANATIONS)
def test_english_explanations_are_detected(text: str) -> None:
    assert has_explanation(text) is True


def test_dry_fact_only_text_is_not_an_explanation() -> None:
    assert has_explanation(DRY_FACT) is False


def test_empty_text_has_no_explanation() -> None:
    assert has_explanation("") is False
    assert has_explanation("   \n\t") is False


def test_detection_is_case_insensitive() -> None:
    assert has_explanation("ПОТОМУ ЧТО пешка g7 ушла вперёд.") is True
    assert has_explanation("White is better BECAUSE the pawn advanced.") is True


def test_detection_ignores_extra_whitespace() -> None:
    assert has_explanation("Поле f6 слабое, потому   что пешка ушла.") is True
    assert has_explanation("Поле f6 слабое, потому\nчто пешка ушла.") is True


def test_markers_cover_russian_and_english_analogs() -> None:
    assert "потому что" in EXPLANATION_MARKERS
    assert "так как" in EXPLANATION_MARKERS
    assert "поэтому" in EXPLANATION_MARKERS
    assert "because" in EXPLANATION_MARKERS
    assert "since" in EXPLANATION_MARKERS


def test_report_contains_explanation_metric() -> None:
    report = _report(_rows(), [GAME], ["club", "gm"], 300.0)

    assert (
        "**Доля объяснений (потому что/так как/because): 100% (2/2)**"
        in report
    )
    assert "| Объяснения |" in report


def test_json_dump_contains_has_explanation(tmp_path: Path) -> None:
    path = tmp_path / "llm_eval.json"

    _dump_json(_rows(), path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert [item["has_explanation"] for item in payload] == [True, True]
