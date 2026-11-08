"""Интеграционные тесты Policy Layer на реальных позициях (T-011/T-012).

Позиции взяты из `tests/fixtures/example.pgn` (Гукеш — Каймер) и двух
известных тактических FEN (мат в 1, вилка); линии движка заморожены по
выдаче Stockfish depth=10, чтобы тест был детерминированным и не требовал
бинарника. Проверяются решения `choose_recommended_move` для всех уровней,
а не только формальные ветки функции.
"""

from __future__ import annotations

import chess
import pytest

from app.engine.models import EngineLine
from app.engine.policy import choose_recommended_move

START = chess.STARTING_FEN


def _line(
    rank: int,
    move_uci: str,
    *,
    score_cp: int | None = None,
    mate: int | None = None,
    pv: list[str] | None = None,
) -> EngineLine:
    return EngineLine(
        multipv=rank,
        depth=10,
        score_cp=score_cp,
        mate=mate,
        move_uci=move_uci,
        pv=[move_uci] if pv is None else pv,
    )


class PolicyCase:
    """Позиция + замороженные линии движка + происхождение."""

    def __init__(
        self, name: str, fen: str, lines: tuple[EngineLine, ...], source: str
    ) -> None:
        self.name = name
        self.fen = fen
        self.lines = lines
        self.source = source


CASES: tuple[PolicyCase, ...] = (
    PolicyCase(
        "legal_queen_sacrifice",
        "rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5",
        (
            _line(1, "f3e5", score_cp=190),
            _line(2, "d2d4", score_cp=108),
            _line(3, "h2h3", score_cp=82),
        ),
        "Légal's mate: Nxe5! начинает жертву ферзя (Bxd1)",
    ),
    PolicyCase(
        "mate_in_one",
        "6k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1",
        (
            _line(1, "a1a8", mate=1),
            _line(2, "g2g3", score_cp=706),
            _line(3, "f2f3", score_cp=693),
        ),
        "мат в 1: Ra8#",
    ),
    PolicyCase(
        "knight_fork",
        "k7/1r1r4/8/2N5/8/8/8/3QK3 w - - 0 1",
        (
            _line(1, "c5d7", score_cp=513),
            _line(2, "d1a4", score_cp=481),
            _line(3, "d1f3", score_cp=260),
        ),
        "вилка: Nxd7 выигрывает ладью, конь защищён ферзём d1",
    ),
    PolicyCase(
        "example_11_e3",
        "r2qr1k1/pppb1pp1/2nppn1p/8/2PP3B/P1Q2N2/1P2PPPP/2KR1B1R w - - 4 11",
        (
            _line(1, "e2e3", score_cp=64),
            _line(2, "c3c2", score_cp=63),
        ),
        "example.pgn, перед 11.e3: почти равные линии (1 cp)",
    ),
    PolicyCase(
        "example_18_fxg5",
        "r2q2k1/1ppb1p2/p1rp1n1p/2r3p1/2P2P1B/P2BP3/1PQ3PP/2KR3R w - - 1 18",
        (
            _line(1, "f4g5", score_cp=-375),
            _line(2, "h4e1", score_cp=-377),
            _line(3, "h4g3", score_cp=-382),
        ),
        "example.pgn, перед 18.fxg5: тактическое напряжение, линии в 2 cp",
    ),
    PolicyCase(
        "example_25_g7",
        "r3k3/1ppbq1PB/p4R1p/3p4/1Pr3PB/P3P3/2Q3P1/2KR4 w - - 1 25",
        (
            _line(1, "g7g8r", mate=2),
            _line(2, "g7g8q", mate=2),
            _line(3, "f6f8", mate=3),
        ),
        "example.pgn, перед 25.g7: превращение с матом в 2",
    ),
)

CASE_IDS = [case.name for case in CASES]


def _hanging_material_after(fen: str, move_uci: str) -> str | None:
    """Какая не-пешечная фигура осталась под боем без защиты после хода."""
    board = chess.Board(fen)
    board.push(board.parse_uci(move_uci))
    if board.is_checkmate() or board.is_stalemate():
        return None
    moved_color = not board.turn
    for square, piece in board.piece_map().items():
        if piece.color != moved_color or piece.piece_type in (
            chess.PAWN,
            chess.KING,
        ):
            continue
        if board.is_attacked_by(board.turn, square) and not board.is_attacked_by(
            moved_color, square
        ):
            return chess.square_name(square)
    return None


# --- gm -------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_gm_takes_top_engine_move_even_in_complex_position(
    case: PolicyCase,
) -> None:
    chosen = choose_recommended_move(case.lines, "gm", fen=case.fen)

    assert chosen.move_uci == case.lines[0].move_uci
    assert chosen is case.lines[0]


# --- club -----------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.name in {"example_11_e3", "example_18_fxg5", "example_25_g7"}],
    ids=["example_11_e3", "example_18_fxg5", "example_25_g7"],
)
def test_club_shows_alternative_when_lines_are_almost_equal(
    case: PolicyCase,
) -> None:
    chosen = choose_recommended_move(case.lines, "club", fen=case.fen)

    assert chosen.move_uci == case.lines[1].move_uci
    assert chosen.move_uci != case.lines[0].move_uci


@pytest.mark.parametrize(
    "case",
    [case for case in CASES if case.name in {"legal_queen_sacrifice", "knight_fork"}],
    ids=["legal_queen_sacrifice", "knight_fork"],
)
def test_club_keeps_top_move_when_gap_is_large(case: PolicyCase) -> None:
    chosen = choose_recommended_move(case.lines, "club", fen=case.fen)

    assert chosen.move_uci == case.lines[0].move_uci


# --- beginner -------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_beginner_never_chooses_a_move_that_loses_material(
    case: PolicyCase,
) -> None:
    chosen = choose_recommended_move(case.lines, "beginner", fen=case.fen)

    assert chosen.move_uci in {line.move_uci for line in case.lines}
    assert _hanging_material_after(case.fen, chosen.move_uci) is None


def test_beginner_rejects_queen_sacrifice_that_hangs_knight() -> None:
    """Nxe5 начинает жертву ферзя, но коня e5 забирают — новичку не советуем."""
    case = next(item for item in CASES if item.name == "legal_queen_sacrifice")

    chosen = choose_recommended_move(case.lines, "beginner", fen=case.fen)

    assert chosen.move_uci == "d2d4"
    assert chosen.move_uci != "f3e5"


def test_beginner_takes_mate_in_one() -> None:
    case = next(item for item in CASES if item.name == "mate_in_one")

    chosen = choose_recommended_move(case.lines, "beginner", fen=case.fen)

    assert chosen.move_uci == "a1a8"
    assert chosen.mate == 1


# --- границы: пустой multipv / нет PV / одна линия ------------------------


@pytest.mark.parametrize("level", ["gm", "club", "beginner"])
def test_empty_multipv_raises(level: str) -> None:
    with pytest.raises(ValueError, match="пуст"):
        choose_recommended_move([], level, fen=START)


@pytest.mark.parametrize("level", ["gm", "club", "beginner"])
def test_lines_without_pv_are_still_usable(level: str) -> None:
    """Движок может не вернуть PV; move_uci и оценка остаются достаточными."""
    lines = [_line(1, "e2e4", score_cp=30, pv=[])]

    chosen = choose_recommended_move(lines, level, fen=START)

    assert chosen.move_uci == "e2e4"


@pytest.mark.parametrize("level", ["gm", "club", "beginner"])
def test_single_line_is_returned_for_all_levels(level: str) -> None:
    lines = [_line(1, "e2e4", score_cp=30)]

    chosen = choose_recommended_move(lines, level, fen=START)

    assert chosen is lines[0]


def test_beginner_skips_unparseable_engine_move() -> None:
    """Битый ход в top-3 не должен становиться рекомендацией."""
    lines = [
        _line(1, "e2e5", score_cp=50),  # формат UCI валиден, ход нелегален
        _line(2, "g1f3", score_cp=40),
    ]

    chosen = choose_recommended_move(lines, "beginner", fen=START)

    assert chosen.move_uci == "g1f3"
