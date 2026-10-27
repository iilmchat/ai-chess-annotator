"""Тесты модуля ingestion (T-007).

Проверяются:

* парсинг реальной партии из `tests/fixtures/example.pgn` (53 полухода,
  метаданные, ECO из заголовка, флаги взятия/шаха, FEN после каждого хода);
* парсинг `tests/fixtures/test_game.pgn` с контролируемыми заголовками и
  ECO из таблицы префиксов (fallback без заголовка `[ECO]`);
* разметка фаз (дебют ≤ 30 полуходов, эндшпиль по материалу/числу фигур);
* ошибки: пустой/мусорный PGN, нелегальный ход, невалидный FEN;
* повторы позиций (основа троекратного повтора).
"""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from app.errors import InvalidFenError, InvalidPgnError
from app.ingestion import (
    detect_eco,
    find_position_repeats,
    lookup_eco,
    normalize_eco,
    parse_fen,
    parse_pgn,
)
from app.ingestion.parser import ENDGAME_MAX_PIECES, OPENING_MAX_PLY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")
TEST_GAME_PGN = (FIXTURES / "test_game.pgn").read_text(encoding="utf-8")

EXPECTED_PLIES = 53
EXPECTED_FULL_MOVES = 27
EXPECTED_ECO = "E21"
LAST_MOVE_SAN = "Bg6+"
FINAL_FEN = "r3k3/1ppb2q1/p4RBp/3p4/1P4PB/P3P3/6P1/2KR4 b - - 1 27"

SPARSE_ENDGAME_PGN = """[SetUp "1"]
[FEN "8/8/8/4k3/8/8/4K3/7R w - - 0 1"]

1. Rh5+ Kd6 2. Rd5+ Ke6 *
"""

#: 10 входных PGN (DoD T-007: «10 тестовых PGN разбираются корректно»):
#: 2 файла-фикстуры + 8 характерных партий/фрагментов разных дебютов.
PARSEABLE_PGNS: list[tuple[str, str]] = [
    ("example_fixture", EXAMPLE_PGN),
    ("test_game_fixture", TEST_GAME_PGN),
    ("ruy_lopez", "1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 5. O-O Be7 1/2-1/2"),
    ("sicilian_najdorf", "1. e4 c5 2. Nf3 d6 3. d4 cxd4 4. Nxd4 Nf6 5. Nc3 a6 0-1"),
    ("french_winawer", "1. e4 e6 2. d4 d5 3. Nc3 Nf6 4. e5 Nfd7 5. f4 c5 1/2-1/2"),
    ("caro_kann", "1. e4 c6 2. d4 d5 3. Nc3 dxe4 4. Nxe4 Bf5 5. Ng3 Bg6 1/2-1/2"),
    ("qgd", "1. d4 d5 2. c4 e6 3. Nc3 Nf6 4. Bg5 Be7 5. e3 O-O 6. Nf3 h6 7. Bh4 b6"),
    ("kid", "1. d4 Nf6 2. c4 g6 3. Nc3 Bg7 4. e4 d6 5. Nf3 O-O 6. Be2 e5 1/2-1/2"),
    ("english", "1. c4 e5 2. Nc3 Nf6 3. Nf3 Nc6 4. g3 d5 5. cxd5 Nxd5 1/2-1/2"),
    ("dutch", "1. d4 f5 2. g3 Nf6 3. Bg2 e6 4. Nf3 Be7 5. O-O O-O 1/2-1/2"),
]


# --- parse_pgn: реальная партия -------------------------------------------


def test_parse_pgn_example_fixture_metadata() -> None:
    game = parse_pgn(EXAMPLE_PGN)

    assert game.metadata.white == "Gukesh, D."
    assert game.metadata.black == "Keymer, Vincent"
    assert game.metadata.event == "6th Prague Masters 2024"
    assert game.metadata.date == "2024.03.06"
    assert game.metadata.result == "1-0"


def test_parse_pgn_example_fixture_move_counts() -> None:
    game = parse_pgn(EXAMPLE_PGN)

    assert game.ply_count == EXPECTED_PLIES
    assert len(game.moves) == EXPECTED_PLIES
    assert game.ply_count // 2 + game.ply_count % 2 == EXPECTED_FULL_MOVES
    assert [move.ply for move in game.moves] == list(
        range(1, EXPECTED_PLIES + 1)
    )
    assert game.moves[0].san == "d4"
    assert game.moves[-1].san == LAST_MOVE_SAN


def test_parse_pgn_example_fixture_eco_from_header() -> None:
    game = parse_pgn(EXAMPLE_PGN)

    assert game.eco_code == EXPECTED_ECO


def test_parse_pgn_fens_match_python_chess_replay() -> None:
    """FEN после каждого хода совпадает с независимым проигрыванием партии."""
    game = parse_pgn(EXAMPLE_PGN)

    board = chess.Board()
    for parsed_move in game.moves:
        board.push_san(parsed_move.san)
        assert parsed_move.fen == board.fen(), f"ply {parsed_move.ply}"

    assert game.final_fen == board.fen()
    assert game.final_fen == FINAL_FEN


def test_parse_pgn_capture_and_check_flags() -> None:
    game = parse_pgn(EXAMPLE_PGN)
    by_ply = {move.ply: move for move in game.moves}

    # 7...Bxc3+ — взятие с шахом.
    capture_with_check = by_ply[14]
    assert capture_with_check.san == "Bxc3+"
    assert capture_with_check.is_capture is True
    assert capture_with_check.is_check is True

    # 14.dxe5 — взятие без шаха.
    assert by_ply[27].san == "dxe5"
    assert by_ply[27].is_capture is True
    assert by_ply[27].is_check is False

    # 1.d4 — тихий ход без взятия и шаха.
    assert by_ply[1].is_capture is False
    assert by_ply[1].is_check is False

    # 27.Bg6+ — последний ход партии, шах без взятия.
    last = by_ply[EXPECTED_PLIES]
    assert last.san == LAST_MOVE_SAN
    assert last.is_capture is False
    assert last.is_check is True


def test_parse_pgn_phase_breakdown_without_endgame() -> None:
    game = parse_pgn(EXAMPLE_PGN)
    phases = game.phase_breakdown

    assert phases.opening_end_ply == OPENING_MAX_PLY == 30
    assert phases.endgame_start_ply is None
    assert phases.opening_end_ply < game.ply_count


# --- parse_pgn: контролируемые метаданные ---------------------------------


def test_parse_pgn_test_game_metadata_and_eco_fallback() -> None:
    game = parse_pgn(TEST_GAME_PGN)

    assert game.metadata.white == "Test White"
    assert game.metadata.black == "Test Black"
    assert game.metadata.event == "Test"
    assert game.metadata.date == "2026.03.30"
    assert game.metadata.result == "1-0"

    assert game.ply_count == EXPECTED_PLIES
    # В файле нет заголовка [ECO] — код определён таблицей префиксов.
    assert game.eco_code == EXPECTED_ECO
    assert game.final_fen == FINAL_FEN


def test_parse_pgn_detects_endgame_in_sparse_position() -> None:
    game = parse_pgn(SPARSE_ENDGAME_PGN)
    phases = game.phase_breakdown

    assert game.ply_count == 4
    # На доске 3 фигуры (< ENDGAME_MAX_PIECES) уже после первого хода.
    assert phases.endgame_start_ply == 1
    assert phases.opening_end_ply == 0
    assert ENDGAME_MAX_PIECES == 10
    assert game.moves[0].is_check is True  # 1.Rh5+


def test_parse_pgn_short_game_keeps_all_plies_in_opening() -> None:
    game = parse_pgn("1. e4 e5 2. Nf3 Nc6 1/2-1/2")

    assert game.ply_count == 4
    assert game.phase_breakdown.opening_end_ply == 4
    assert game.phase_breakdown.endgame_start_ply is None
    # Заголовков нет — метаданные пустые, а не «?».
    assert game.metadata.white is None
    assert game.metadata.date is None
    assert game.metadata.result == "1/2-1/2"
    assert game.eco_code == "C44"  # 1.e4 e5 2.Nf3 Nc6


# --- parse_pgn: набор входных PGN ----------------------------------------


@pytest.mark.parametrize(("label", "pgn"), PARSEABLE_PGNS, ids=[p[0] for p in PARSEABLE_PGNS])
def test_parse_pgn_accepts_known_games(label: str, pgn: str) -> None:
    """Каждый из 10 входных PGN разбирается и проигрывается без ошибок."""
    game = parse_pgn(pgn)

    assert game.ply_count > 0, label
    assert game.final_fen, label

    board = chess.Board()
    for move in game.moves:
        board.push_san(move.san)
    assert board.fen() == game.final_fen, label


# --- parse_pgn: ошибки ----------------------------------------------------


def test_parse_pgn_empty_raises() -> None:
    with pytest.raises(InvalidPgnError, match="пуст"):
        parse_pgn("   \n  ")


def test_parse_pgn_garbage_raises() -> None:
    with pytest.raises(InvalidPgnError):
        parse_pgn("это не PGN, а просто текст без ходов")


def test_parse_pgn_illegal_move_raises() -> None:
    with pytest.raises(InvalidPgnError, match="ошибк"):
        parse_pgn("1. e5 e4 *")


@pytest.mark.xfail(
    reason="KI-012: python-chess молча обрывает разбор на неизвестном токене",
    strict=True,
)
def test_parse_pgn_unknown_token_is_rejected() -> None:
    with pytest.raises(InvalidPgnError):
        parse_pgn("1. e4 e5 2. Zz9 *")


# --- parse_fen ------------------------------------------------------------


def test_parse_fen_starting_position() -> None:
    board = parse_fen(chess.STARTING_FEN)

    assert isinstance(board, chess.Board)
    assert board.fen() == chess.STARTING_FEN
    assert board.is_valid()


def test_parse_fen_strips_whitespace() -> None:
    board = parse_fen(f"  {chess.STARTING_FEN}  \n")

    assert board.fen() == chess.STARTING_FEN


def test_parse_fen_invalid_syntax_raises() -> None:
    with pytest.raises(InvalidFenError, match="Некорректный FEN"):
        parse_fen("not-a-fen")


def test_parse_fen_impossible_position_raises() -> None:
    with pytest.raises(InvalidFenError, match="Невозможная позиция"):
        parse_fen("8/8/8/8/8/8/8/8 w - - 0 1")


def test_parse_fen_empty_raises() -> None:
    with pytest.raises(InvalidFenError, match="пуст"):
        parse_fen("   ")


# --- ECO ------------------------------------------------------------------


def test_eco_normalize() -> None:
    assert normalize_eco("e21") == "E21"
    assert normalize_eco(" E21 ") == "E21"
    assert normalize_eco("Z99") is None
    assert normalize_eco("E2") is None
    assert normalize_eco("") is None
    assert normalize_eco(None) is None


def test_eco_lookup_prefers_longest_prefix() -> None:
    nimzo = ["d4", "Nf6", "c4", "e6", "Nc3", "Bb4", "Nf3", "d6"]
    assert lookup_eco(nimzo) == "E21"
    assert lookup_eco(["d4", "Nf6", "c4", "e6", "Nc3", "Bb4"]) == "E20"
    assert lookup_eco(["e4", "e5", "Nf3", "Nc6", "Bb5"]) == "C60"
    assert lookup_eco(["a3"]) is None
    assert lookup_eco([]) is None


def test_detect_eco_header_wins_over_table() -> None:
    moves = ["e4", "e5", "Nf3", "Nc6", "Bb5"]

    assert detect_eco("C99", moves) == "C99"
    assert detect_eco(None, moves) == "C60"
    assert detect_eco("?", moves) == "C60"  # «пустой» заголовок — не код


# --- повторы позиций ------------------------------------------------------


def test_find_position_repeats_with_initial_fen() -> None:
    game = parse_pgn("1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 *")
    repeats = find_position_repeats(game.moves, initial_fen=chess.STARTING_FEN)

    assert [(item.first_ply, item.repeat_ply) for item in repeats] == [(0, 4), (1, 5)]
    assert repeats[0].board_fen == " ".join(chess.STARTING_FEN.split()[:4])


def test_find_position_repeats_without_initial_fen() -> None:
    """Без стартовой позиции повтор самой расстановки не виден — только ходы."""
    game = parse_pgn("1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 *")

    repeats = find_position_repeats(game.moves)

    assert [(item.first_ply, item.repeat_ply) for item in repeats] == [(1, 5)]


def test_find_position_repeats_none_in_test_game() -> None:
    game = parse_pgn(TEST_GAME_PGN)

    assert find_position_repeats(game.moves, initial_fen=chess.STARTING_FEN) == []
