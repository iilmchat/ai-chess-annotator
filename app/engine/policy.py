"""Policy Layer: детерминированный выбор рекомендуемого хода (T-011).

Не «топ-1 движка», а **функция от уровня** (SPEC §2.5):

* ``gm`` — лучший ход движка (`multipv[0]`): гроссмейстеру не нужно
  объяснять альтернативу, ему нужна точность;
* ``club`` — `multipv[0]`, но если два первых хода почти равны
  (разница < 20 cp), берётся второй: клубному игроку полезно увидеть
  альтернативу с почти той же оценкой;
* ``beginner`` — из top-3 отбрасываются «зевки» (оставляет фигуру под
  боем) и «ранний ферзь» (вывод ферзя до развития), приоритет отдаётся
  развитию, центру и рокирровке. Если ничего не подошло — `multipv[0]`.

Для принципов новичка нужна сама позиция, поэтому `fen` — необязательный
параметр: без него политика деградирует до `multipv[0]` (и это явно
задокументировано, а не «угадано»).
"""

from __future__ import annotations

import chess

from app.engine.models import EngineLine
from app.errors import InvalidLevelError
from app.ingestion.parser import parse_fen

__all__ = ["CLUB_EQUAL_LINE_CP", "choose_recommended_move"]

#: Порог «почти равных» линий для клубного уровня (SPEC §2.5).
CLUB_EQUAL_LINE_CP = 20

#: До какого полного хода ход ферзём считается ранним.
EARLY_QUEEN_FULLMOVES = 3

#: Минимальное число развитых лёгких фигур, чтобы ферзь не считался «ранним».
MIN_DEVELOPED_MINORS = 2

_VALUE: dict[int, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_CENTER_SQUARES = {chess.D4, chess.E4, chess.D5, chess.E5}


def choose_recommended_move(
    multipv: list[EngineLine] | tuple[EngineLine, ...],
    level: str,
    *,
    fen: str | None = None,
) -> EngineLine:
    """Выбрать «рекомендуемый ход под уровень».

    Args:
        multipv: линии движка, отсортированные по рангу (первая — лучшая).
        level: ``beginner`` / ``club`` / ``gm``.
        fen: позиция перед ходом; нужна для принципов новичка. Без неё
            уровень ``beginner`` ведёт себя как ``club`` по первому ходу.

    Returns:
        Одна из линий `multipv`.

    Raises:
        ValueError: `multipv` пуст.
        InvalidLevelError: неизвестный уровень.
    """
    if not multipv:
        raise ValueError("multipv пуст: нечего рекомендовать")

    if level == "gm":
        return multipv[0]
    if level == "club":
        return _choose_club(multipv)
    if level == "beginner":
        if fen is None:
            return multipv[0]
        return _choose_beginner(multipv, fen)
    raise InvalidLevelError(
        f"Неизвестный уровень {level!r}; ожидается beginner, club или gm"
    )


def _choose_club(
    multipv: list[EngineLine] | tuple[EngineLine, ...],
) -> EngineLine:
    if len(multipv) < 2:
        return multipv[0]
    gap = abs(_line_cp(multipv[0]) - _line_cp(multipv[1]))
    return multipv[1] if gap < CLUB_EQUAL_LINE_CP else multipv[0]


def _choose_beginner(
    multipv: list[EngineLine] | tuple[EngineLine, ...], fen: str
) -> EngineLine:
    board = parse_fen(fen)

    acceptable: list[tuple[EngineLine, chess.Move]] = []
    for line in list(multipv)[:3]:
        try:
            move = board.parse_uci(line.move_uci)
        except ValueError:
            continue
        if _is_early_queen(board, move):
            continue
        if _leaves_piece_hanging(board, move):
            continue
        acceptable.append((line, move))

    for line, move in acceptable:
        if _follows_principles(board, move):
            return line
    if acceptable:
        return acceptable[0][0]
    return multipv[0]


# --- эвристики ------------------------------------------------------------


def _line_cp(line: EngineLine) -> int:
    """Оценка линии в сантипешках (мат — большое число), за сторону хода."""
    if line.mate is not None:
        sign = 1 if line.mate > 0 else -1
        return sign * (10_000 - 100 * min(abs(line.mate), 99))
    return line.score_cp or 0


def _developed_minors(board: chess.Board) -> int:
    """Сколько лёгких фигур стороны уже ушло с последней горизонтали."""
    home_rank = 0 if board.turn == chess.WHITE else 7
    return sum(
        1
        for square, piece in board.piece_map().items()
        if piece.color == board.turn
        and piece.piece_type in (chess.KNIGHT, chess.BISHOP)
        and chess.square_rank(square) != home_rank
    )


def _is_early_queen(board: chess.Board, move: chess.Move) -> bool:
    """Вывод ферзя до развития в первые ходы — типичная ошибка новичка."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type != chess.QUEEN:
        return False
    if board.fullmove_number > EARLY_QUEEN_FULLMOVES:
        return False
    return _developed_minors(board) < MIN_DEVELOPED_MINORS


def _leaves_piece_hanging(board: chess.Board, move: chess.Move) -> bool:
    """После хода остаётся фигура (не пешка), которую бьют и не защищают."""
    mover_color = board.turn
    opponent = not mover_color
    board.push(move)
    try:
        for square, piece in board.piece_map().items():
            if piece.color != mover_color or piece.piece_type in (
                chess.PAWN,
                chess.KING,
            ):
                continue
            if board.is_attacked_by(opponent, square) and not board.is_attacked_by(
                mover_color, square
            ):
                return True
        return False
    finally:
        board.pop()


def _follows_principles(board: chess.Board, move: chess.Move) -> bool:
    """Развитие, центр или рокировка — «базовые принципы» из SPEC §2.5."""
    if board.is_castling(move):
        return True
    piece = board.piece_at(move.from_square)
    if piece is None:
        return False
    if piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        home_rank = 0 if piece.color == chess.WHITE else 7
        return chess.square_rank(move.from_square) == home_rank
    if piece.piece_type == chess.PAWN:
        return move.to_square in _CENTER_SQUARES
    return False
