"""Тесты Policy Layer (T-011).

Проверяется детерминированный выбор хода под уровень: `gm` — топ-1,
`club` — альтернатива при почти равных линиях, `beginner` — отбрасывание
«раннего ферзя» и «зевков» (оставляет фигуру под боем).
"""

from __future__ import annotations

import pytest

from app.engine.models import EngineLine
from app.engine.policy import CLUB_EQUAL_LINE_CP, choose_recommended_move
from app.errors import InvalidLevelError

#: После 1.e4 e5, белые ходят.
AFTER_E4_E5 = "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"
#: После 1.e4 e5 2.Nf3 Nc6, белые ходят.
AFTER_NF3_NC6 = "r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 4 3"


def _line(
    rank: int, move_uci: str, score_cp: int | None = None, mate: int | None = None
) -> EngineLine:
    return EngineLine(
        multipv=rank,
        depth=12,
        score_cp=score_cp,
        mate=mate,
        move_uci=move_uci,
        pv=[move_uci],
    )


def _queen_line() -> list[EngineLine]:
    """Движок «советует» 2.Qh5 — типичная ошибка новичка."""
    return [
        _line(1, "d1h5", 30),
        _line(2, "f1c4", 25),
        _line(3, "g1f3", 20),
    ]


def _hanging_knight_lines() -> list[EngineLine]:
    """Движок «советует» Nxe5, теряя коня после Nxe5."""
    return [
        _line(1, "f3e5", 20),
        _line(2, "f1c4", 20),
        _line(3, "b1c3", 15),
    ]


# --- gm -------------------------------------------------------------------


def test_gm_always_takes_top_move() -> None:
    lines = _queen_line()

    chosen = choose_recommended_move(lines, "gm", fen=AFTER_E4_E5)

    assert chosen.move_uci == "d1h5"


def test_gm_ignores_almost_equal_lines() -> None:
    lines = [_line(1, "e2e4", 25), _line(2, "d2d4", 24)]

    assert choose_recommended_move(lines, "gm").move_uci == "e2e4"


# --- club -----------------------------------------------------------------


def test_club_takes_top_move_when_gap_is_large() -> None:
    lines = [_line(1, "e2e4", 40), _line(2, "d2d4", 10)]

    assert choose_recommended_move(lines, "club").move_uci == "e2e4"


def test_club_shows_alternative_when_lines_are_equal() -> None:
    lines = [_line(1, "e2e4", 25), _line(2, "d2d4", 25 - (CLUB_EQUAL_LINE_CP - 1))]

    assert choose_recommended_move(lines, "club").move_uci == "d2d4"


def test_club_keeps_top_move_at_exact_threshold() -> None:
    lines = [_line(1, "e2e4", 25), _line(2, "d2d4", 25 - CLUB_EQUAL_LINE_CP)]

    assert choose_recommended_move(lines, "club").move_uci == "e2e4"


def test_club_with_single_line() -> None:
    lines = [_line(1, "e2e4", 25)]

    assert choose_recommended_move(lines, "club").move_uci == "e2e4"


def test_club_compares_mate_with_centipawns() -> None:
    lines = [_line(1, "d1h5", mate=2), _line(2, "f1c4", 30)]

    # Мат (≈9800) несравним с +30 — альтернативу не показываем.
    assert choose_recommended_move(lines, "club").move_uci == "d1h5"


# --- beginner -------------------------------------------------------------


def test_beginner_skips_early_queen_move() -> None:
    chosen = choose_recommended_move(_queen_line(), "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "f1c4", "ферзь на 2-м ходу — не рекомендация новичку"


def test_beginner_skips_move_that_hangs_a_knight() -> None:
    chosen = choose_recommended_move(
        _hanging_knight_lines(), "beginner", fen=AFTER_NF3_NC6
    )

    assert chosen.move_uci == "f1c4", "Nxe5 теряет коня — новичку не советуем"


def test_beginner_falls_back_to_top_move_when_all_are_bad() -> None:
    lines = [_line(1, "d1h5", 30), _line(2, "d1f3", 25), _line(3, "d1g4", 20)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "d1h5"


def test_beginner_without_fen_returns_top_move() -> None:
    chosen = choose_recommended_move(_queen_line(), "beginner")

    assert chosen.move_uci == "d1h5"


def test_beginner_takes_first_acceptable_without_principle() -> None:
    # Ни один ход не «развивающий», но и не зевок: берём первый приемлемый.
    lines = [_line(1, "a2a3", 10), _line(2, "h2h3", 8)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "a2a3"


def test_beginner_prefers_development_over_passive_pawn_move() -> None:
    lines = [_line(1, "a2a3", 10), _line(2, "g1f3", 8)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "g1f3"


# --- KI-020: мат в свою пользу разрешён всегда ---------------------------


def test_beginner_allows_early_queen_when_it_mates() -> None:
    """KI-020: Qh5 — «ранний ферзь», но с матом в 2 хода он разрешён."""
    lines = [_line(1, "d1h5", mate=2), _line(2, "f1c4", 25), _line(3, "g1f3", 20)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "d1h5"


def test_beginner_allows_hanging_piece_when_it_mates() -> None:
    """KI-020: Nxe5 «висит», но ведёт к мату — новичку показываем."""
    lines = [_line(1, "f3e5", mate=1), _line(2, "f1c4", 20), _line(3, "b1c3", 15)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_NF3_NC6)

    assert chosen.move_uci == "f3e5"


def test_beginner_does_not_bypass_filters_for_losing_mate() -> None:
    """Отрицательный мат (получаем мы) — не повод обходить фильтры."""
    lines = [_line(1, "d1h5", mate=-2), _line(2, "f1c4", 25), _line(3, "g1f3", 20)]

    chosen = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert chosen.move_uci == "f1c4"


def test_beginner_and_gm_disagree_on_premature_queen() -> None:
    """DoD T-012: рекомендации новичку и гроссмейстеру различаются."""
    lines = _queen_line()

    gm_move = choose_recommended_move(lines, "gm", fen=AFTER_E4_E5)
    beginner_move = choose_recommended_move(lines, "beginner", fen=AFTER_E4_E5)

    assert gm_move.move_uci == "d1h5"
    assert beginner_move.move_uci == "f1c4"
    assert gm_move.move_uci != beginner_move.move_uci


# --- ошибки ---------------------------------------------------------------


def test_unknown_level_raises() -> None:
    with pytest.raises(InvalidLevelError):
        choose_recommended_move(_queen_line(), "master")


def test_empty_multipv_raises() -> None:
    with pytest.raises(ValueError, match="пуст"):
        choose_recommended_move([], "club")
