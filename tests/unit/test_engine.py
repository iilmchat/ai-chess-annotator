"""Тесты Engine Service (T-008).

* классификация ходов — чистый unit, без движка;
* контракт ошибок (нет бинарника, плохой FEN, неизвестный уровень) — без
  запуска процесса;
* интеграционные тесты — с **реальным** Stockfish по пути из настроек
  (`STOCKFISH_PATH`); если бинарника нет, они скипаются (KI-006).

Интеграционные тесты используют малые глубины (`depth=5…8`), чтобы не
тратить минуты; бенчмарк уровня `club` проверяется отдельно.
"""

from __future__ import annotations

import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

import chess
import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.engine import ENGINE_PROFILES, EngineService, resolve_profile
from app.engine.models import EngineLine
from app.errors import (
    EngineNotFoundError,
    InvalidFenError,
    InvalidLevelError,
    InvalidPgnError,
)
from app.ingestion import parse_pgn

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
EXAMPLE_PGN = (FIXTURES / "example.pgn").read_text(encoding="utf-8")

START = chess.STARTING_FEN
#: Лимит из SPEC.md §2.2 для уровня «клуб».
CLUB_LIMIT_S = 1.0
#: Допуск на IPC/чтение поверх `movetime` (сам поиск ограничен 1000 мс).
BENCHMARK_TOLERANCE_S = 0.15


def _stockfish_available() -> bool:
    settings = get_settings()
    candidate = Path(settings.stockfish_path)
    return candidate.is_file() or shutil.which(settings.stockfish_path) is not None


requires_engine = pytest.mark.skipif(
    not _stockfish_available(),
    reason="Stockfish не найден (KI-006): задайте STOCKFISH_PATH",
)


@pytest.fixture
async def engine() -> AsyncIterator[EngineService]:
    service = EngineService()
    try:
        yield service
    finally:
        await service.aclose()


def _unstarted_service() -> EngineService:
    """Сервис, который не должен дойти до запуска процесса."""
    return EngineService(executable=Path("__no_such_stockfish__"))


# --- классификация --------------------------------------------------------


@pytest.mark.parametrize(
    ("delta_cp", "is_best", "expected"),
    [
        (0, False, "best"),
        (0, True, "best"),
        (-25, False, "best"),
        (1, False, "good"),
        (49, False, "good"),
        (50, False, "inaccuracy"),
        (99, False, "inaccuracy"),
        (100, False, "mistake"),
        (200, False, "mistake"),
        (201, False, "blunder"),
        (1500, False, "blunder"),
        (300, True, "best"),
    ],
)
def test_classify_move_boundaries(
    delta_cp: int, is_best: bool, expected: str
) -> None:
    assert EngineService.classify_move(delta_cp, is_best) == expected


def test_level_profiles_match_spec() -> None:
    assert ENGINE_PROFILES["beginner"].depth == 12
    assert ENGINE_PROFILES["beginner"].multipv == 1
    assert ENGINE_PROFILES["beginner"].movetime_ms == 200

    assert ENGINE_PROFILES["club"].depth == 18
    assert ENGINE_PROFILES["club"].multipv == 3
    assert ENGINE_PROFILES["club"].movetime_ms == 1000

    assert ENGINE_PROFILES["gm"].depth == 28
    assert ENGINE_PROFILES["gm"].multipv == 5
    assert ENGINE_PROFILES["gm"].movetime_ms == 5000


def test_resolve_profile_rejects_unknown_level() -> None:
    with pytest.raises(InvalidLevelError, match="master"):
        resolve_profile("master")


# --- модели ---------------------------------------------------------------


def test_engine_line_requires_exactly_one_score() -> None:
    with pytest.raises(ValidationError):
        EngineLine(multipv=1, depth=3, move_uci="e2e4", pv=["e2e4"])
    with pytest.raises(ValidationError):
        EngineLine(
            multipv=1, depth=3, score_cp=10, mate=2, move_uci="e2e4", pv=["e2e4"]
        )


def test_engine_line_accepts_cp_and_mate() -> None:
    cp_line = EngineLine(multipv=1, depth=5, score_cp=31, move_uci="e2e4", pv=["e2e4"])
    mate_line = EngineLine(multipv=2, depth=5, mate=3, move_uci="d1h5", pv=["d1h5"])

    assert cp_line.score_cp == 31
    assert cp_line.mate is None
    assert mate_line.mate == 3
    assert mate_line.score_cp is None


# --- контракт ошибок (без запуска движка) ---------------------------------


async def test_engine_not_found_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing-stockfish.exe"
    service = EngineService(Settings(stockfish_path=str(missing)))

    with pytest.raises(EngineNotFoundError, match="STOCKFISH_PATH"):
        await service.analyze(START, "beginner")

    with pytest.raises(EngineNotFoundError):
        _ = service.executable_path

    await service.aclose()


async def test_analyze_rejects_invalid_fen() -> None:
    service = _unstarted_service()

    with pytest.raises(InvalidFenError):
        await service.analyze("not-a-fen", "beginner")


async def test_analyze_rejects_unknown_level() -> None:
    service = _unstarted_service()

    with pytest.raises(InvalidLevelError):
        await service.analyze(START, "master")


async def test_analyze_validates_overrides() -> None:
    service = _unstarted_service()

    with pytest.raises(ValueError, match="depth"):
        await service.analyze(START, "beginner", depth=0)
    with pytest.raises(ValueError, match="multipv"):
        await service.analyze(START, "beginner", multipv=0)
    with pytest.raises(ValueError, match="movetime"):
        await service.analyze(START, "beginner", movetime_ms=0)


async def test_analyze_game_moves_empty_list() -> None:
    service = _unstarted_service()

    assert await service.analyze_game_moves([], "beginner") == []


async def test_analyze_game_moves_rejects_inconsistent_history() -> None:
    game = parse_pgn(EXAMPLE_PGN)
    service = _unstarted_service()

    with pytest.raises(InvalidPgnError, match="нелегален"):
        await service.analyze_game_moves(
            game.moves[:2],
            "beginner",
            initial_fen="8/8/8/4k3/8/8/4K3/7R w - - 0 1",
        )


# --- интеграция с реальным Stockfish --------------------------------------


@requires_engine
async def test_executable_path_resolves(engine: EngineService) -> None:
    path = engine.executable_path

    assert path.is_file()
    assert "stockfish" in path.name.lower()


@requires_engine
async def test_analyze_startpos_returns_legal_best_move(engine: EngineService) -> None:
    lines = await engine.analyze(START, "beginner", depth=5)

    assert len(lines) == 1
    line = lines[0]
    assert line.multipv == 1
    assert line.depth >= 1
    assert chess.Move.from_uci(line.move_uci) in chess.Board().legal_moves
    assert line.move_uci in {"e2e4", "d2d4", "g1f3", "c2c4"}
    assert line.pv[0] == line.move_uci
    assert (line.score_cp is None) != (line.mate is None)


@requires_engine
async def test_analyze_multipv_returns_distinct_lines(engine: EngineService) -> None:
    lines = await engine.analyze(START, "club", depth=8, multipv=3, movetime_ms=2000)

    assert [line.multipv for line in lines] == [1, 2, 3]
    moves = [line.move_uci for line in lines]
    assert len(set(moves)) == 3
    for line in lines:
        assert line.pv[0] == line.move_uci
        assert len(line.pv) >= 1


@requires_engine
async def test_club_analysis_within_one_second(engine: EngineService) -> None:
    """Бенчмарк SPEC.md §2.2: уровень «клуб» — ~1 с на позицию."""
    await engine.analyze(START, "beginner", depth=5, movetime_ms=500)  # прогрев

    started = time.perf_counter()
    lines = await engine.analyze(START, "club")
    elapsed = time.perf_counter() - started

    assert len(lines) == 3
    assert elapsed <= CLUB_LIMIT_S + BENCHMARK_TOLERANCE_S, (
        f"club analyze занял {elapsed * 1000:.0f} мс "
        f"(лимит {CLUB_LIMIT_S * 1000:.0f} мс + {BENCHMARK_TOLERANCE_S * 1000:.0f} мс)"
    )


@requires_engine
async def test_analyze_game_moves_first_three_moves(engine: EngineService) -> None:
    game = parse_pgn(EXAMPLE_PGN)

    analyses = await engine.analyze_game_moves(
        game.moves[:6], "beginner", initial_fen=game.initial_fen
    )

    assert len(analyses) == 6
    assert [item.ply for item in analyses] == [1, 2, 3, 4, 5, 6]
    assert [item.played_move_san for item in analyses] == [
        move.san for move in game.moves[:6]
    ]
    assert analyses[0].played_move_uci == "d2d4"

    allowed = {"best", "brilliant", "good", "inaccuracy", "mistake", "blunder"}
    for item in analyses:
        assert item.classification in allowed
        assert item.delta_cp >= 0
        assert item.best_move_san
        # Разность оценок согласована с delta_cp.
        assert item.delta_cp == max(0, item.eval_before - item.eval_after)
        # Лучший ход действительно легален в позиции до хода.
        board = chess.Board(game.initial_fen)
        for previous in analyses[: item.ply - 1]:
            board.push_san(previous.played_move_san)
        assert board.parse_uci(item.best_move_uci) in board.legal_moves
