"""Тесты детектора мотивов (T-010)."""

from __future__ import annotations

from pathlib import Path

import chess
import pytest

from app.engine.models import EngineLine
from app.errors import InvalidFenError
from app.ingestion import parse_pgn
from app.motifs import MAX_MOTIFS, detect_motifs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")

KNOWN_TYPES: set[str] = {
    "passed_pawn",
    "weak_square",
    "open_file",
    "open_diagonal",
    "king_safety",
    "fork",
    "pin",
    "mate_threat",
    "winning_capture",
}


def _types(fen: str, lines: list[EngineLine] | None = None) -> set[str]:
    return {motif.type for motif in detect_motifs(fen, lines or [])}


# --- доска ----------------------------------------------------------------


def test_start_position_has_king_safety_only() -> None:
    motifs = detect_motifs(chess.STARTING_FEN)

    assert {motif.type for motif in motifs} == {"king_safety"}
    assert len(motifs) == 2  # оба короля в центре при ферзях
    assert all(motif.importance == 4 for motif in motifs)


def test_passed_pawn_detected() -> None:
    motifs = detect_motifs("8/8/8/3P4/8/8/8/K6k w - - 0 1")

    passed = [motif for motif in motifs if motif.type == "passed_pawn"]
    assert len(passed) == 1
    assert "d5" in passed[0].description
    assert passed[0].importance >= 3


def test_no_passed_pawns_at_start() -> None:
    assert "passed_pawn" not in _types(chess.STARTING_FEN)


def test_weak_square_next_to_king() -> None:
    # У белых нет пешек e2 и g2 → f3 нельзя защитить пешкой.
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPP1P1P/RNBQKBNR w KQkq - 0 1"

    weak = [m for m in detect_motifs(fen) if m.type == "weak_square"]
    assert len(weak) == 1
    assert "f3" in weak[0].description


def test_no_weak_squares_in_solid_pawn_structure() -> None:
    assert "weak_square" not in _types(chess.STARTING_FEN)


def test_open_file_detected() -> None:
    motifs = detect_motifs("4k3/8/8/8/8/8/8/R3K3 w Q - 0 1")

    opened = [m for m in motifs if m.type == "open_file"]
    assert len(opened) == 1
    assert opened[0].description == "Открытая линия a"


def test_fork_detected() -> None:
    # Конь c5 бьёт обе ладьи b7 и d7.
    motifs = detect_motifs("k7/1r1r4/8/2N5/8/8/8/4K3 w - - 0 1")

    forks = [m for m in motifs if m.type == "fork"]
    assert len(forks) == 1
    assert "b7" in forks[0].description and "d7" in forks[0].description


def test_pin_detected() -> None:
    # Ладья e7 связана белой ладьёй e1 с королём e8.
    motifs = detect_motifs("4k3/4r3/8/8/8/8/8/4RK2 w - - 0 1")

    pins = [m for m in motifs if m.type == "pin"]
    assert len(pins) == 1
    assert "e7" in pins[0].description


def test_mate_threat_from_engine_line() -> None:
    lines = [EngineLine(multipv=1, depth=6, mate=1, move_uci="a1a8", pv=["a1a8"])]

    motifs = detect_motifs("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", lines)

    mate = [m for m in motifs if m.type == "mate_threat"]
    assert len(mate) == 1
    assert mate[0].importance == 5
    assert "1" in mate[0].description


def test_winning_capture_from_engine_line() -> None:
    # Rxd5: ладья берёт незащищённого ферзя (+400).
    lines = [EngineLine(multipv=1, depth=6, score_cp=900, move_uci="d1d5", pv=["d1d5"])]

    motifs = detect_motifs("4k3/8/8/3q4/8/8/8/3RK3 w - - 0 1", lines)

    captures = [m for m in motifs if m.type == "winning_capture"]
    assert len(captures) == 1
    assert "d5" in captures[0].description


def test_invalid_fen_raises() -> None:
    with pytest.raises(InvalidFenError):
        detect_motifs("not-a-fen")


# --- 20 позиций из реальной партии ---------------------------------------


def test_detects_motifs_on_twenty_positions_from_fixture() -> None:
    game = parse_pgn(EXAMPLE_PGN)
    fens = [game.initial_fen, *[move.fen for move in game.moves]]

    # 20 позиций, равномерно по всей партии (включая последнюю).
    indices = [round(index * (len(fens) - 1) / 19) for index in range(20)]
    sampled = [fens[index] for index in indices]
    assert len(sampled) == 20

    seen: set[str] = set()
    for fen in sampled:
        motifs = detect_motifs(fen)
        assert len(motifs) <= MAX_MOTIFS
        for motif in motifs:
            assert motif.type in KNOWN_TYPES
            assert 1 <= motif.importance <= 5
            assert motif.description
        seen.update(motif.type for motif in motifs)

    # За партию детектор обязан увидеть проходную пешку (g6) и открытую
    # линию (f), иначе эвристики бесполезны.
    assert "passed_pawn" in seen, seen
    assert "open_file" in seen, seen


def test_passed_pawn_in_fixture_after_g7() -> None:
    """25.g7 — реальная проходная пешка из партии Гукеш — Каймер."""
    game = parse_pgn(EXAMPLE_PGN)
    index = next(
        position for position, move in enumerate(game.moves) if move.san == "g7"
    )

    passed = [
        motif
        for motif in detect_motifs(game.moves[index].fen)
        if motif.type == "passed_pawn"
    ]

    assert any("g7" in motif.description for motif in passed), passed
