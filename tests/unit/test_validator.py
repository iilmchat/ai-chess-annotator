"""Тесты валидатора ответов LLM (T-015)."""

from __future__ import annotations

import chess
import pytest

from app.engine.validator import (
    extract_moves_from_text,
    validate_llm_response,
    validate_moves,
)
from app.errors import InvalidFenError

START = chess.STARTING_FEN


# --- извлечение ходов -----------------------------------------------------


def test_extract_moves_from_commentary_text() -> None:
    text = (
        "После 1. d4 Nf6 2. c4 e6 белые могли сыграть **Nc3**, а затем O-O. "
        "Рассмотрим 0-0-0, удар exd5+ и превращение e8=Q#."
    )

    moves = extract_moves_from_text(text)

    assert moves == ["d4", "Nf6", "c4", "e6", "Nc3", "O-O", "O-O-O", "exd5+", "e8=Q#"]


def test_extract_ignores_non_moves() -> None:
    text = "Ходы Ke9, i4 и слово abcd не являются шахматными ходами. Оценка +120 cp."

    assert extract_moves_from_text(text) == []


def test_extract_keeps_moves_with_punctuation_and_markdown() -> None:
    text = "Лучше было `e4`, не *d4*; после Bb5+ чёрные ответили a6."

    assert extract_moves_from_text(text) == ["e4", "d4", "Bb5+", "a6"]


def test_extract_empty_text() -> None:
    assert extract_moves_from_text("") == []


# --- валидация ------------------------------------------------------------


def test_all_mainline_moves_are_legal() -> None:
    legal, illegal = validate_moves(["d4", "Nf6", "c4", "e6"], START)

    assert legal is True
    assert illegal == []


def test_illegal_move_is_reported() -> None:
    legal, illegal = validate_moves(["Ke8"], START)

    assert legal is False
    assert illegal == ["Ke8"]


def test_illegal_moves_are_deduplicated() -> None:
    legal, illegal = validate_moves(["Nf3", "Ke8", "Ke8"], START)

    assert legal is False
    assert illegal == ["Ke8"]


def test_alternative_is_legal_only_with_mainline() -> None:
    # Фрагмент комментария про вариант: «…Bb5, затем Ba4».
    # Без ходов партии эти ходы невозможны из старта; с ходами партии
    # (после Nc6 и после a6) — это легальные ходы в позициях партии.
    moves = ["Bb5", "Ba4"]
    mainline = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"]

    legal_without, illegal_without = validate_moves(moves, START)
    legal_with, illegal_with = validate_moves(
        moves, START, mainline_san=mainline
    )

    assert (legal_without, illegal_without) == (False, ["Bb5", "Ba4"])
    assert (legal_with, illegal_with) == (True, [])


def test_castling_and_promotion_are_legal() -> None:
    castle_fen = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
    legal, illegal = validate_moves(["O-O", "O-O-O"], castle_fen)
    assert (legal, illegal) == (True, [])

    promo_fen = "8/P7/8/8/8/8/8/K6k w - - 0 1"
    legal, illegal = validate_moves(["a8=Q"], promo_fen)
    assert (legal, illegal) == (True, [])


def test_empty_text_is_legal() -> None:
    assert validate_moves([], START) == (True, [])


def test_invalid_fen_raises() -> None:
    with pytest.raises(InvalidFenError):
        validate_moves(["e4"], "not-a-fen")


async def test_validate_llm_response_extracts_and_checks() -> None:
    text = "Партия началась 1. e4 e5 2. Nf3, но затем «Nc3» было неточностью."

    legal, illegal = await validate_llm_response(text, START, None)  # type: ignore[arg-type]

    assert legal is True
    assert illegal == []


async def test_validate_llm_response_flags_hallucination() -> None:
    text = "После 1. e4 e5 чёрные сыграли Qxf7."

    legal, illegal = await validate_llm_response(text, START, None)  # type: ignore[arg-type]

    assert legal is False
    assert illegal == ["Qxf7"]
