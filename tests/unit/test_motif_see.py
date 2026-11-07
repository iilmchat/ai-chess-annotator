"""SEE для висящих фигур и вилок (KI-019, итерация 64, шаг 2).

Проверяем упрощённый static exchange evaluation: фигура без защиты или
атакованная более дешёвой фигурой считается выигрываемой; равный размен —
нет. На этом построены мотив `hanging_piece` и фильтр `fork`.
"""

from __future__ import annotations

import chess

from app.motifs import detect_motifs, is_piece_hanging

#: Чёрный конь d6 под боем белого коня e4 и без защиты.
HANGING_KNIGHT_FEN = "4k3/8/3n4/8/4N3/8/8/4K3 w - - 0 1"
#: Тот же конь d6, но защищён пешкой c7: равный размен невыгоден.
DEFENDED_KNIGHT_FEN = "4k3/2p5/3n4/8/4N3/8/8/4K3 w - - 0 1"
#: Чёрный ферзь d5 под боем белой пешки c4, защищён пешкой e6:
#: пешка против ферзя выигрывает материал, несмотря на защиту.
PAWN_ATTACKS_QUEEN_FEN = "4k3/8/4p3/3q4/2P5/8/8/4K3 w - - 0 1"
#: Белая ладья e1 против чёрной ладьи e8, защищённой королём f8: равный
#: размен, «висящей» фигура не считается.
EQUAL_ROOKS_FEN = "4rk2/8/8/8/8/8/8/4R1K1 w - - 0 1"
#: Белый конь f5 бьёт двух чёрных коней d4/d6, но оба защищены пешками
#: c5/c7 ровно на 3: вилка не выигрывает материал и не должна срабатывать.
DEFENDED_FORK_FEN = "4k3/2p5/3n4/2p2N2/3n4/8/8/4K3 w - - 0 1"


def _board(fen: str) -> chess.Board:
    return chess.Board(fen)


# --- is_piece_hanging ------------------------------------------------------


def test_undefended_knight_is_hanging() -> None:
    board = _board(HANGING_KNIGHT_FEN)

    assert is_piece_hanging(board, chess.D6) is True


def test_defended_equal_piece_is_not_hanging() -> None:
    board = _board(DEFENDED_KNIGHT_FEN)

    assert is_piece_hanging(board, chess.D6) is False


def test_cheap_attacker_wins_against_defended_queen() -> None:
    board = _board(PAWN_ATTACKS_QUEEN_FEN)

    assert is_piece_hanging(board, chess.D5) is True


def test_equal_rook_trade_is_not_hanging() -> None:
    board = _board(EQUAL_ROOKS_FEN)

    assert is_piece_hanging(board, chess.E8) is False


def test_empty_square_and_king_are_never_hanging() -> None:
    board = _board(HANGING_KNIGHT_FEN)

    assert is_piece_hanging(board, chess.A1) is False
    assert is_piece_hanging(board, chess.E8) is False  # король


# --- мотив hanging_piece ---------------------------------------------------


def test_hanging_piece_motif_is_detected() -> None:
    motifs = detect_motifs(HANGING_KNIGHT_FEN)

    hanging = [motif for motif in motifs if motif.type == "hanging_piece"]
    assert len(hanging) == 1
    assert "d6" in hanging[0].description
    assert "конь" in hanging[0].description
    assert hanging[0].importance == 3


def test_protected_piece_does_not_emit_hanging_motif() -> None:
    motifs = detect_motifs(DEFENDED_KNIGHT_FEN)

    assert all(motif.type != "hanging_piece" for motif in motifs)


def test_hanging_queen_has_max_importance() -> None:
    motifs = detect_motifs(PAWN_ATTACKS_QUEEN_FEN)

    hanging = [motif for motif in motifs if motif.type == "hanging_piece"]
    assert len(hanging) == 1
    assert "d5" in hanging[0].description
    assert hanging[0].importance == 5


# --- fork ------------------------------------------------------------------


def test_fork_with_hanging_target_is_reported() -> None:
    # Конь c5 бьёт ладьи b7 и d7; b7 защищена королём, d7 — нет.
    motifs = detect_motifs("k7/1r1r4/8/2N5/8/8/8/4K3 w - - 0 1")

    forks = [motif for motif in motifs if motif.type == "fork"]
    assert len(forks) == 1
    assert "b7" in forks[0].description and "d7" in forks[0].description


def test_fork_of_equally_defended_pieces_is_not_reported() -> None:
    motifs = detect_motifs(DEFENDED_FORK_FEN)

    assert all(motif.type != "fork" for motif in motifs)
