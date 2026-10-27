"""Парсинг PGN/FEN в нормализованную партию (T-007).

Публичный контракт:

* :func:`parse_pgn` — PGN → :class:`~app.ingestion.models.ParsedGame`;
* :func:`parse_fen` — FEN → проверенный :class:`chess.Board`;
* :func:`find_position_repeats` — повторы позиций (основа троекратного
  повтора); полноценный детект транспозиций относительно теории требует
  ECO/opening book, см. KI-011.

Ошибки входа всегда поднимаются как :class:`~app.errors.InvalidPgnError` /
:class:`~app.errors.InvalidFenError`, а не как ``ValueError`` из
`python-chess`: вызывающий код (API, воркеры) не должен зависеть от
сторонней библиотеки.

Ограничения текущей реализации (зафиксированы явно, чтобы не было
«тихих» сюрпризов):

* читается только **первая** партия в PGN (batch-ingest — T-017);
* варианты (variations) игнорируются — берётся mainline;
* `python-chess` **молча останавливается** на нераспознанном токене
  (например, ``2. Zz9``) и возвращает укороченную партию без ошибок —
  валидация «до конца ли прочитан PGN» появится вместе со строгим
  ingest-ом (KI-012).
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from typing import Final

import chess
import chess.pgn

from app.errors import InvalidFenError, InvalidPgnError
from app.ingestion.eco import detect_eco
from app.ingestion.models import (
    GameMetadata,
    ParsedGame,
    ParsedMove,
    PhaseBreakdown,
    PositionRepeat,
)

__all__ = [
    "ENDGAME_MATERIAL_THRESHOLD_CP",
    "ENDGAME_MAX_PIECES",
    "OPENING_MAX_PLY",
    "PIECE_VALUES",
    "find_position_repeats",
    "parse_fen",
    "parse_pgn",
]

#: Дебют — первые 15 ходов (30 полуходов), SPEC.md §2.1 / постановка T-007.
OPENING_MAX_PLY: Final[int] = 30

#: Порог эндшпиля по материалу (в сантипешках, обе стороны).
ENDGAME_MATERIAL_THRESHOLD_CP: Final[int] = 2000

#: Порог эндшпиля по количеству фигур на доске (включая королей).
ENDGAME_MAX_PIECES: Final[int] = 10

#: Классические ценности фигур (сантипешки).
PIECE_VALUES: Final[dict[int, int]] = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

#: Значения заголовков PGN, которые означают «не задано».
_EMPTY_HEADERS: Final[frozenset[str]] = frozenset({"", "?", "??", "????.??.??"})


def parse_fen(fen_string: str) -> chess.Board:
    """Проверить FEN и вернуть доску.

    Args:
        fen_string: FEN позиции (допускаются лишние пробелы по краям).

    Returns:
        ``chess.Board`` с валидной позицией.

    Raises:
        InvalidFenError: синтаксическая ошибка FEN или позиция невозможна
            (нет короля, слишком много фигур, невозможный шах и т. п.).
    """
    if not isinstance(fen_string, str):
        raise InvalidFenError("FEN должен быть строкой")
    candidate = fen_string.strip()
    if not candidate:
        raise InvalidFenError("FEN пуст")

    try:
        board = chess.Board(candidate)
    except ValueError as exc:
        raise InvalidFenError(f"Некорректный FEN: {exc}") from exc

    if not board.is_valid():
        reasons = board.status()
        raise InvalidFenError(f"Невозможная позиция в FEN: {reasons}")

    return board


def parse_pgn(pgn_string: str) -> ParsedGame:
    """Разобрать PGN в нормализованную партию.

    Args:
        pgn_string: текст PGN (заголовки и/или ходы).

    Returns:
        ``ParsedGame`` с ходами, метаданными, ECO и границами фаз.

    Raises:
        InvalidPgnError: PGN пуст, не содержит ходов, содержит нелегальный
            ход или ошибки разбора.
    """
    if not isinstance(pgn_string, str):
        raise InvalidPgnError("PGN должен быть строкой")
    if not pgn_string.strip():
        raise InvalidPgnError("PGN пуст: нет ни заголовков, ни ходов")

    game = chess.pgn.read_game(io.StringIO(pgn_string))
    if game is None:
        raise InvalidPgnError("PGN не распознан: нет ни заголовков, ни ходов")
    if game.errors:
        details = "; ".join(str(error) for error in game.errors)
        raise InvalidPgnError(f"PGN содержит ошибки: {details}")

    board = game.board()
    initial_fen = board.fen()
    moves: list[ParsedMove] = []
    endgame_start_ply: int | None = None

    for ply, node in enumerate(game.mainline(), start=1):
        move = node.move
        if move is None:
            raise InvalidPgnError(f"Полуход {ply} не содержит хода")

        is_capture = board.is_capture(move)
        board.push(move)
        moves.append(
            ParsedMove(
                ply=ply,
                san=node.san(),
                fen=board.fen(),
                is_capture=is_capture,
                is_check=board.is_check(),
            )
        )
        if endgame_start_ply is None and _is_endgame(board):
            endgame_start_ply = ply

    if not moves:
        raise InvalidPgnError(
            "PGN не содержит ходов: заголовки без movetext не анализируются"
        )

    phase = _build_phase_breakdown(
        total_plies=len(moves), endgame_start_ply=endgame_start_ply
    )
    metadata = GameMetadata(
        white=_clean_header(game.headers.get("White")),
        black=_clean_header(game.headers.get("Black")),
        event=_clean_header(game.headers.get("Event")),
        date=_clean_header(game.headers.get("Date")),
        result=_clean_header(game.headers.get("Result")),
    )

    return ParsedGame(
        metadata=metadata,
        moves=moves,
        eco_code=detect_eco(game.headers.get("ECO"), [move.san for move in moves]),
        phase_breakdown=phase,
        initial_fen=initial_fen,
        final_fen=board.fen(),
    )


def find_position_repeats(
    moves: Sequence[ParsedMove], *, initial_fen: str | None = None
) -> list[PositionRepeat]:
    """Найти повторяющиеся позиции (одинаковые фигуры, очередь хода, права).

    Счётчики хода и правило 50 ходов из FEN игнорируются — сравнивается
    «шахматная» часть позиции. ``initial_fen`` (обычно стартовая позиция
    партии) позволяет поймать повтор самой начальной расстановки.

    Args:
        moves: полуходы в порядке игры.
        initial_fen: FEN позиции до первого полухода (необязательно).

    Returns:
        Список повторов в порядке появления; при тройном повторе позиции
        будет две записи, обе ссылаются на первое появление.
    """
    seen: dict[str, int] = {}
    if initial_fen is not None:
        seen[_board_key(initial_fen)] = 0

    repeats: list[PositionRepeat] = []
    for move in moves:
        key = _board_key(move.fen)
        first_ply = seen.get(key)
        if first_ply is None:
            seen[key] = move.ply
        else:
            repeats.append(
                PositionRepeat(first_ply=first_ply, repeat_ply=move.ply, board_fen=key)
            )
    return repeats


def _board_key(fen: str) -> str:
    """FEN без счётчиков хода и полухода (первые 4 поля)."""
    return " ".join(fen.split()[:4])


def _clean_header(value: str | None) -> str | None:
    """Заголовок PGN → строка или ``None`` (``?``/``????.??.??`` считаются пустыми)."""
    if value is None:
        return None
    cleaned = value.strip()
    return None if cleaned in _EMPTY_HEADERS else cleaned


def _material_cp(board: chess.Board) -> int:
    """Суммарный материал обеих сторон в сантипешках."""
    return sum(
        PIECE_VALUES.get(piece.piece_type, 0) for piece in board.piece_map().values()
    )


def _is_endgame(board: chess.Board) -> bool:
    """Позиция считается эндшпилем по материалу или по числу фигур."""
    return (
        _material_cp(board) < ENDGAME_MATERIAL_THRESHOLD_CP
        or len(board.piece_map()) < ENDGAME_MAX_PIECES
    )


def _build_phase_breakdown(
    *, total_plies: int, endgame_start_ply: int | None
) -> PhaseBreakdown:
    """Границы фаз: дебют ≤ 30 полуходов, эндшпиль — по первому «пустому» ходу.

    Если позиция стала эндшпильной ещё в дебюте (учебные окончания, партии
    с FEN-стартом), граница дебюта сдвигается назад, чтобы фазы не
    пересекались.
    """
    opening_end_ply = min(OPENING_MAX_PLY, total_plies)
    if endgame_start_ply is not None and endgame_start_ply <= opening_end_ply:
        opening_end_ply = max(0, endgame_start_ply - 1)
    return PhaseBreakdown(
        opening_end_ply=opening_end_ply, endgame_start_ply=endgame_start_ply
    )
