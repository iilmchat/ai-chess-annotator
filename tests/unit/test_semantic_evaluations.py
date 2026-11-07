"""Валидация числовых оценок в комментарии (KI-023, итерация 64).

Модель может написать «оценка +5.0» или «мат в 3», хотя в пакете фактов
другая оценка. Проверяем извлечение, сверку с `MoveAnalysis` с допуском
±0.5 пешки, совпадение мата и вырезание offending-предложения.
"""

from __future__ import annotations

import chess
import pytest

from app.commentary.semantic import (
    EVAL_TOLERANCE_PAWNS,
    extract_evaluation_claims,
    strip_invalid_claims,
    validate_semantic_claims,
)
from app.engine.models import MoveAnalysis

START = chess.STARTING_FEN


def _analysis(
    *,
    ply: int = 1,
    eval_before: int = 10,
    eval_after: int = 20,
    mate_after: int | None = None,
) -> MoveAnalysis:
    """Минимальный `MoveAnalysis` для сверки оценок.

    `eval_before`/`eval_after` — с точки зрения сделавшего ход; для
    нечётного ply это белые, для чётного — чёрные (знак инвертируется).
    """
    white_move = ply % 2 == 1
    return MoveAnalysis(
        ply=ply,
        played_move_san="e4" if white_move else "e5",
        played_move_uci="e2e4" if white_move else "e7e5",
        eval_before=eval_before,
        eval_after=eval_after,
        best_move_uci="e2e4" if white_move else "e7e5",
        best_move_san="e4" if white_move else "e5",
        delta_cp=0,
        classification="best",
        mate_after=mate_after,
    )


# --- извлечение -----------------------------------------------------------


def test_extract_signed_evaluations() -> None:
    claims = extract_evaluation_claims(
        "Сначала оценка +0.50, затем -1.2, а также −0.3."
    )

    assert [(claim.kind, claim.pawns) for claim in claims] == [
        ("evaluation", 0.50),
        ("evaluation", -1.2),
        ("evaluation", -0.3),
    ]


def test_extract_mate_in_ru_and_en() -> None:
    claims = extract_evaluation_claims("Мат в 3 за белых. Mate in 5.")

    assert [(claim.kind, claim.mate_in) for claim in claims] == [
        ("mate", 3),
        ("mate", 5),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Ход e4 был лучшим.",
        "Оценка 0.5 без знака.",
        "Мат в трёх ходах.",
        "Версия 1.2.",
    ],
)
def test_extract_no_evaluation_claims(text: str) -> None:
    assert extract_evaluation_claims(text) == []


def test_evaluation_claim_keeps_whole_sentence() -> None:
    claims = extract_evaluation_claims(
        "Позиция сложная. Оценка +0.20 за белых. Ход d4 хорош."
    )

    assert len(claims) == 1
    assert claims[0].sentence == "Оценка +0.20 за белых."
    assert claims[0].text == "+0.20"


# --- сверка с фактами ------------------------------------------------------


def test_correct_evaluation_is_valid() -> None:
    assert validate_semantic_claims(
        "Движок оценил позицию в +0.20.",
        START,
        analyses=[_analysis(eval_after=20)],
    ) == (True, [])


def test_evaluation_within_tolerance_is_valid() -> None:
    # Факт +0.20, текст +0.50: разница 0.30 ≤ допуска 0.50.
    assert validate_semantic_claims(
        "Оценка +0.50 за белых.",
        START,
        analyses=[_analysis(eval_after=20)],
    ) == (True, [])


def test_wrong_evaluation_is_rejected() -> None:
    valid, problems = validate_semantic_claims(
        "Оценка +3.0 за белых.",
        START,
        analyses=[_analysis(eval_after=20)],
    )

    assert valid is False
    assert problems == ["+3.0"]


def test_black_evaluation_is_normalized_to_white_perspective() -> None:
    # ply=2 (чёрные), eval_after=+20 для чёрных = -0.20 за белых.
    assert validate_semantic_claims(
        "Оценка -0.20 за белых.",
        START,
        analyses=[_analysis(ply=2, eval_after=20)],
    ) == (True, [])


def test_matching_mate_is_valid_regardless_of_side() -> None:
    assert validate_semantic_claims(
        "Мат в 3 за чёрных.",
        START,
        analyses=[_analysis(mate_after=-3)],
    ) == (True, [])


def test_wrong_mate_is_rejected() -> None:
    valid, problems = validate_semantic_claims(
        "Здесь мат в 5.",
        START,
        analyses=[_analysis(mate_after=3)],
    )

    assert valid is False
    assert problems == ["мат в 5"]


def test_evaluations_are_not_checked_without_analyses() -> None:
    # Обратная совместимость: без analyses сверять не с чем.
    assert validate_semantic_claims("Оценка +3.0.", START) == (True, [])


def test_structural_and_evaluation_problems_coexist() -> None:
    valid, problems = validate_semantic_claims(
        "Слон на e6 активен. Оценка +3.0.",
        START,
        analyses=[_analysis(eval_after=20)],
    )

    assert valid is False
    assert problems == ["слон на e6", "+3.0"]


def test_strip_removes_wrong_evaluation_sentence() -> None:
    text = "Оценка +3.0 за белых. Ход e4 был лучшим."

    stripped = strip_invalid_claims(text, ["+3.0"])

    assert stripped == "Ход e4 был лучшим."


def test_tolerance_constant_is_half_pawn() -> None:
    assert EVAL_TOLERANCE_PAWNS == 0.5
