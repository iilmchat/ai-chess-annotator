"""Тесты семантической валидации комментариев LLM (KI-023, итерация 37).

Проверяются четыре слоя:

* извлечение утверждений «фигура на поле» / «связка» (RU + EN);
* сверка утверждений с позициями партии (mainline + PV-линии);
* вырезание невалидных предложений на последней попытке;
* подсказка для повторной генерации и конфигурационный флаг.
"""

from __future__ import annotations

import chess
import pytest

from app.commentary.semantic import (
    extract_semantic_claims,
    semantic_retry_prompt,
    semantic_warning,
    strip_invalid_claims,
    validate_semantic_claims,
)
from app.config import Settings
from app.errors import InvalidFenError

START = chess.STARTING_FEN

#: Чёрный слон на e6 — «правильная» позиция для проверок существования.
BISHOP_E6_FEN = "rnbqkbnr/pppp1ppp/4b3/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
#: Белый конь на d2.
KNIGHT_D2_FEN = "4k3/8/8/8/8/8/3N4/4K3 w - - 0 1"


# --- извлечение утверждений ----------------------------------------------


def test_extract_ru_piece_on_square() -> None:
    claims = extract_semantic_claims("Слон на e6 — ключевая фигура.")

    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "piece_on_square"
    assert claim.piece == chess.BISHOP
    assert claim.color is None
    assert claim.square == chess.E6
    assert claim.text.lower() == "слон на e6"
    assert "Слон на e6" in claim.sentence


def test_extract_ru_colored_piece_on_square() -> None:
    claims = extract_semantic_claims("Чёрный конь на d4 доминирует.")

    assert len(claims) == 1
    claim = claims[0]
    assert (claim.piece, claim.color, claim.square) == (
        chess.KNIGHT,
        chess.BLACK,
        chess.D4,
    )


def test_extract_ru_pin() -> None:
    claims = extract_semantic_claims("Связка пешки f7 решает.")

    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "pin"
    assert (claim.piece, claim.color, claim.square) == (
        chess.PAWN,
        None,
        chess.F7,
    )


def test_extract_ru_pin_with_preposition_is_single_claim() -> None:
    """«Связка коня на d2» — одно утверждение, а не pin + piece_on_square."""
    claims = extract_semantic_claims("Связка коня на d2.")

    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "pin"
    assert (claim.piece, claim.square) == (chess.KNIGHT, chess.D2)


def test_extract_en_piece_on_square() -> None:
    claims = extract_semantic_claims("The bishop on e6 is strong.")

    assert len(claims) == 1
    claim = claims[0]
    assert (claim.piece, claim.color, claim.square) == (
        chess.BISHOP,
        None,
        chess.E6,
    )


def test_extract_en_colored_piece_on_square() -> None:
    claims = extract_semantic_claims("The white rook on a1 defends the king.")

    assert len(claims) == 1
    claim = claims[0]
    assert (claim.piece, claim.color, claim.square) == (
        chess.ROOK,
        chess.WHITE,
        chess.A1,
    )


def test_extract_en_pin_of_pawn() -> None:
    claims = extract_semantic_claims("The pin of the f7 pawn is decisive.")

    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "pin"
    assert (claim.piece, claim.square) == (chess.PAWN, chess.F7)


def test_extract_en_pinned_passive_voice() -> None:
    claims = extract_semantic_claims("The e6 bishop is pinned.")

    assert len(claims) == 1
    claim = claims[0]
    assert claim.kind == "pin"
    assert (claim.piece, claim.square) == (chess.BISHOP, chess.E6)


def test_extract_multiple_claims_in_order() -> None:
    claims = extract_semantic_claims("Слон на e6 и чёрная ладья на a8.")

    assert [(item.piece, item.color, item.square) for item in claims] == [
        (chess.BISHOP, None, chess.E6),
        (chess.ROOK, chess.BLACK, chess.A8),
    ]


def test_extract_claim_sentence_is_whole_sentence() -> None:
    text = "Позиция сложная. Слон на e6 был активен. Ход d4 хорош."

    claims = extract_semantic_claims(text)

    assert len(claims) == 1
    assert claims[0].sentence == "Слон на e6 был активен."


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Позиция сложная, белые лучше.",
        "После 1. d4 Nf6 2. c4 e6 белые получили перевес.",
        "Ключевое поле e4 и центр.",
        "Ход на e4 был лучшим.",
        "Лучше было Bb5, чем Nf3.",
        "Слон — сильная фигура.",
        "Слон e6 смотрит на ферзевый фланг.",
    ],
)
def test_extract_no_claims(text: str) -> None:
    assert extract_semantic_claims(text) == []


# --- сверка с позициями ---------------------------------------------------


def test_claim_true_in_some_position() -> None:
    assert validate_semantic_claims("Слон на e6.", BISHOP_E6_FEN) == (True, [])


def test_claim_absent_everywhere_is_a_problem() -> None:
    valid, problems = validate_semantic_claims("Слон на e6 — угроза.", START)

    assert valid is False
    assert problems == ["слон на e6"]


def test_claim_with_wrong_color_is_a_problem() -> None:
    valid, problems = validate_semantic_claims(
        "Белый слон на e6 контролирует поле.", BISHOP_E6_FEN
    )

    assert valid is False
    assert problems == ["белый слон на e6"]


def test_claim_with_matching_color_is_ok() -> None:
    assert validate_semantic_claims(
        "Чёрный слон на e6.", BISHOP_E6_FEN
    ) == (True, [])


def test_mainline_positions_allow_later_claim() -> None:
    mainline = ["e4", "e5", "Nf3", "Nc6", "Bb5"]

    legal, problems = validate_semantic_claims(
        "Слон на b5 занял активную позицию.", START, mainline_san=mainline
    )
    legal_without, problems_without = validate_semantic_claims(
        "Слон на b5 занял активную позицию.", START
    )

    assert (legal, problems) == (True, [])
    assert legal_without is False
    assert problems_without == ["слон на b5"]


def test_extra_fens_allow_pv_claim() -> None:
    board = chess.Board(START)
    board.push_san("e4")
    board.push_san("e5")
    board.push_san("Nf3")
    pv_fen = board.fen()

    legal, problems = validate_semantic_claims(
        "Конь на f3.", START, extra_fens=[pv_fen]
    )
    legal_without, problems_without = validate_semantic_claims(
        "Конь на f3.", START
    )

    assert (legal, problems) == (True, [])
    assert legal_without is False
    assert problems_without == ["конь на f3"]


def test_pin_claim_with_existing_piece_is_ok() -> None:
    assert validate_semantic_claims(
        "Связка коня на d2.", KNIGHT_D2_FEN
    ) == (True, [])


def test_pin_claim_with_absent_piece_is_a_problem() -> None:
    valid, problems = validate_semantic_claims("Связка коня на d2.", START)

    assert valid is False
    assert problems == ["связка коня на d2"]


def test_claim_holds_in_at_least_one_position_of_game() -> None:
    """Ферзь стоял на h5 в середине партии — утверждение корректно."""
    mainline = ["e4", "e5", "Qh5", "Nc6", "Qxe5+"]

    assert validate_semantic_claims(
        "Ферзь на h5 атаковал f7.", START, mainline_san=mainline
    ) == (True, [])


def test_problems_are_deduplicated_and_stable() -> None:
    text = "Слон на e6. Ладья на a5. Слон на e6."

    valid, problems = validate_semantic_claims(text, START)

    assert valid is False
    assert problems == ["слон на e6", "ладья на a5"]


def test_empty_text_is_valid() -> None:
    assert validate_semantic_claims("", START) == (True, [])
    assert validate_semantic_claims("Партия была напряжённой.", START) == (
        True,
        [],
    )


def test_invalid_fen_raises() -> None:
    with pytest.raises(InvalidFenError):
        validate_semantic_claims("Слон на e6.", "not-a-fen")


# --- вырезание невалидных предложений ------------------------------------


def test_strip_removes_only_bad_sentence() -> None:
    text = "Слон на e6 был активен. Белые сыграли d4. Позиция сложная."

    stripped = strip_invalid_claims(text, ["слон на e6"])

    assert stripped == "Белые сыграли d4. Позиция сложная."


def test_strip_all_invalid_returns_original() -> None:
    text = "Слон на e6 был активен. Конь на d4 доминировал."

    stripped = strip_invalid_claims(text, ["слон на e6", "конь на d4"])

    assert stripped == text


def test_strip_preserves_paragraphs() -> None:
    text = (
        "Слон на e6 был активен. Белые сыграли d4.\n\n"
        "Пешка на f7 стояла плохо. Ладья на d1 контролировала линию."
    )

    stripped = strip_invalid_claims(text, ["слон на e6", "пешка на f7"])

    assert stripped == (
        "Белые сыграли d4.\n\nЛадья на d1 контролировала линию."
    )


def test_strip_collapses_double_spaces() -> None:
    text = "Ход d4.  Слон на e6.  Ладья на a1."

    stripped = strip_invalid_claims(text, ["слон на e6"])

    assert stripped == "Ход d4. Ладья на a1."


def test_strip_is_case_and_yo_insensitive() -> None:
    text = "Чёрный слон на e6 был активен. Ход d4 хорош."

    stripped = strip_invalid_claims(text, ["черный слон на e6"])

    assert stripped == "Ход d4 хорош."


def test_strip_without_problems_returns_text_unchanged() -> None:
    text = "Слон на e6 был активен."

    assert strip_invalid_claims(text, []) == text
    assert strip_invalid_claims(text, ["фраза, которой нет в тексте"]) == text


# --- подсказка для повторной генерации -----------------------------------


def test_semantic_retry_prompt_ru() -> None:
    prompt = semantic_retry_prompt(
        "ПАКЕТ ФАКТОВ", ["слон на e6"], language="ru"
    )

    assert "ПАКЕТ ФАКТОВ" in prompt
    assert "слон на e6" in prompt
    assert "Перепиши" in prompt


def test_semantic_retry_prompt_en() -> None:
    prompt = semantic_retry_prompt(
        "FACT PACKAGE", ["bishop on e6"], language="en"
    )

    assert "FACT PACKAGE" in prompt
    assert "bishop on e6" in prompt
    assert "WARNING" in prompt
    assert "Rewrite" in prompt


def test_semantic_warning_has_no_package() -> None:
    warning = semantic_warning(["слон на e6"], language="ru")

    assert "слон на e6" in warning
    assert "ПАКЕТ" not in warning


# --- конфигурация ---------------------------------------------------------


def test_semantic_validation_enabled_by_default() -> None:
    assert Settings(_env_file=None).semantic_validation_enabled is True


def test_semantic_validation_can_be_disabled_by_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEMANTIC_VALIDATION_ENABLED", "false")

    assert Settings(_env_file=None).semantic_validation_enabled is False


def test_semantic_validation_can_be_disabled_explicitly() -> None:
    settings = Settings(
        _env_file=None, semantic_validation_enabled=False
    )

    assert settings.semantic_validation_enabled is False
