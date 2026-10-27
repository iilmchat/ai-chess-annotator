"""Детектор шахматных мотивов (T-010).

Детерминированные эвристики поверх `python-chess` + (опционально) линии
Stockfish:

| Тип | Что ищет | Источник |
| :--- | :--- | :--- |
| `passed_pawn` | пешка без пешек соперника на своей и соседних файлах впереди | доска |
| `weak_square` | «дыра» рядом с королём: поле, которое не может защитить своя пешка | доска |
| `open_file` | файл без пешек, на котором стоит ладья/ферзь | доска |
| `open_diagonal` | большая диагональ без пешек с слоном/ферзём | доска |
| `king_safety` | король в центре при ферзях / отсутствует пешка f у рокированного короля | доска |
| `fork` | фигура стороны, которая ходит, атакует ≥ 2 ценные цели | доска |
| `pin` | у соперника есть связанная фигура | доска |
| `mate_threat` | лучшая линия движка ведёт к мату | `multipv` |
| `winning_capture` | лучший ход — взятие с выигрышем материала | `multipv` |

Ограничения (KI-019): это эвристики, а не полный анализ. Нет SEE, нет
учёта тактических защит, «вилка» ищется только по прямым атакам фигуры.
Мотив — подсказка для текста, а не истина в последней инстанции.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import chess

from app.ingestion.parser import parse_fen
from app.motifs.models import Motif

if TYPE_CHECKING:  # разрываем цикл импорта: engine.models импортирует Motif
    from app.engine.models import EngineLine

__all__ = ["MAX_MOTIFS", "detect_motifs"]

#: Сколько мотивов максимум отдаём на позицию (иначе текст и БД тонут в шуме).
MAX_MOTIFS = 6

#: Минимальная ценность цели для «вилки».
VALUABLE_TARGET = 300

#: Сколько пешек должно быть у стороны, чтобы искать «дыры» у её короля.
MIN_PAWNS_FOR_HOLES = 4

_VALUE: dict[int, int] = {
    chess.PAWN: 100,
    chess.KNIGHT: 300,
    chess.BISHOP: 300,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

_CENTER_FILES = {3, 4}


def detect_motifs(
    fen: str, multipv: Sequence[EngineLine] | None = None
) -> list[Motif]:
    """Найти мотивы в позиции.

    Args:
        fen: FEN позиции (валидируется через `app.ingestion.parse_fen`).
        multipv: линии Stockfish для этой позиции (для тактических мотивов).

    Returns:
        До `MAX_MOTIFS` мотивов, отсортированных по важности (важные первыми),
        без дублей по `(type, description)`.

    Raises:
        InvalidFenError: FEN некорректен.
    """
    board = parse_fen(fen)
    motifs = [
        *_passed_pawns(board),
        *_weak_squares(board),
        *_open_files(board),
        *_open_diagonals(board),
        *_king_safety(board),
        *_tactical(board),
        *_engine_motifs(board, multipv or []),
    ]

    unique: dict[tuple[str, str], Motif] = {}
    for motif in motifs:
        unique.setdefault((motif.type, motif.description), motif)
    ordered = sorted(unique.values(), key=lambda item: item.importance, reverse=True)
    return ordered[:MAX_MOTIFS]


# --- пешки ----------------------------------------------------------------


def _is_passed_pawn(board: chess.Board, square: chess.Square, color: chess.Color) -> bool:
    """Нет ли впереди (на своём и соседних файлах) пешек соперника."""
    file_index = chess.square_file(square)
    rank = chess.square_rank(square)
    files = range(max(0, file_index - 1), min(7, file_index + 1) + 1)
    ranks = range(rank + 1, 8) if color == chess.WHITE else range(0, rank)
    for file in files:
        for target_rank in ranks:
            piece = board.piece_at(chess.square(file, target_rank))
            if piece and piece.piece_type == chess.PAWN and piece.color != color:
                return False
    return True


def _passed_pawns(board: chess.Board) -> list[Motif]:
    motifs: list[Motif] = []
    for square, piece in board.piece_map().items():
        if piece.piece_type != chess.PAWN:
            continue
        if not _is_passed_pawn(board, square, piece.color):
            continue
        rank = chess.square_rank(square)
        advanced = rank if piece.color == chess.WHITE else 7 - rank
        # Пешка на своей 1–2-й горизонтали ещё не фактор: иначе в позициях
        # без пешек соперника каждая пешка объявлялась бы «проходной».
        if advanced < 2:
            continue
        importance = 5 if advanced >= 5 else 4 if advanced == 4 else 3
        side = "белых" if piece.color == chess.WHITE else "чёрных"
        motifs.append(
            Motif(
                type="passed_pawn",
                description=f"Проходная пешка {chess.square_name(square)} у {side}",
                importance=importance,
            )
        )
    return motifs


# --- слабые поля ----------------------------------------------------------

def _pawn_defends(
    board: chess.Board, square: chess.Square, color: chess.Color
) -> bool:
    """Может ли пешка `color` защитить `square` (стоит на диагонали снизу)."""
    file_index = chess.square_file(square)
    rank = chess.square_rank(square)
    defender_rank = rank - 1 if color == chess.WHITE else rank + 1
    if not 0 <= defender_rank <= 7:
        return False
    for defender_file in (file_index - 1, file_index + 1):
        if not 0 <= defender_file <= 7:
            continue
        piece = board.piece_at(chess.square(defender_file, defender_rank))
        if piece and piece.piece_type == chess.PAWN and piece.color == color:
            return True
    return False


def _weak_squares(board: chess.Board) -> list[Motif]:
    """«Дыры» рядом с королём: пустые поля, которые не защитить пешкой.

    Ограничения, без которых мотив превращается в шум:

    * у стороны должно быть хотя бы `MIN_PAWNS_FOR_HOLES` пешек — в
      пешечных окончаниях «дырой» становится почти каждое поле, и мотив
      теряет смысл;
    * поле — 3…6-я горизонталь стороны (1–2-я — тыл, не «дыра»);
    * центральные файлы исключены (там борьба за центр, а не дыра);
    * не больше одного мотива на короля (ближайшее к нему поле).
    """
    motifs: list[Motif] = []
    for color in (chess.WHITE, chess.BLACK):
        king = board.king(color)
        if king is None:
            continue
        own_pawns = sum(
            1
            for piece in board.piece_map().values()
            if piece.piece_type == chess.PAWN and piece.color == color
        )
        if own_pawns < MIN_PAWNS_FOR_HOLES:
            continue

        side = "белых" if color == chess.WHITE else "чёрных"
        candidates: list[chess.Square] = []
        for square in chess.SQUARES:
            if chess.square_distance(king, square) > 2:
                continue
            if chess.square_file(square) in _CENTER_FILES:
                continue
            rank = chess.square_rank(square)
            own_rank = rank if color == chess.WHITE else 7 - rank
            if own_rank < 2 or own_rank > 5:  # 3…6-я горизонталь стороны
                continue
            if board.piece_at(square) is not None:
                continue
            if _pawn_defends(board, square, color):
                continue
            candidates.append(square)

        if not candidates:
            continue
        nearest = min(candidates, key=lambda square: chess.square_distance(king, square))
        motifs.append(
            Motif(
                type="weak_square",
                description=(
                    f"Слабое поле {chess.square_name(nearest)} рядом с королём "
                    f"{side} (пешка его не защищает)"
                ),
                importance=4,
            )
        )
    return motifs


# --- линии и диагонали ----------------------------------------------------

def _open_files(board: chess.Board) -> list[Motif]:
    pawn_files = {
        chess.square_file(square)
        for square, piece in board.piece_map().items()
        if piece.piece_type == chess.PAWN
    }
    king_files = {
        chess.square_file(king)
        for color in (chess.WHITE, chess.BLACK)
        if (king := board.king(color)) is not None
    }

    motifs: list[Motif] = []
    for file in range(8):
        if file in pawn_files:
            continue
        heavy = [
            chess.square(file, rank)
            for rank in range(8)
            if (piece := board.piece_at(chess.square(file, rank))) is not None
            and piece.piece_type in (chess.ROOK, chess.QUEEN)
        ]
        if not heavy:
            continue
        near_king = any(abs(file - king_file) <= 1 for king_file in king_files)
        motifs.append(
            Motif(
                type="open_file",
                description=f"Открытая линия {chr(ord('a') + file)}",
                importance=4 if near_king else 3,
            )
        )
        if len(motifs) >= 2:
            break
    return motifs


def _open_diagonals(board: chess.Board) -> list[Motif]:
    # Две большие диагонали: a1–h8 (файл растёт вместе с рангом) и h1–a8.
    dialects: dict[str, list[chess.Square]] = {
        "a1–h8": [chess.square(index, index) for index in range(8)],
        "h1–a8": [chess.square(index, 7 - index) for index in range(8)],
    }

    motifs: list[Motif] = []
    for name, squares in dialects.items():
        pieces = [
            piece
            for square in squares
            if (piece := board.piece_at(square)) is not None
        ]
        if any(piece.piece_type == chess.PAWN for piece in pieces):
            continue
        if not any(piece.piece_type in (chess.BISHOP, chess.QUEEN) for piece in pieces):
            continue
        motifs.append(
            Motif(
                type="open_diagonal",
                description=f"Открытая диагональ {name}",
                importance=3,
            )
        )
    return motifs


# --- безопасность короля --------------------------------------------------

def _king_safety(board: chess.Board) -> list[Motif]:
    motifs: list[Motif] = []
    queens_on_board = any(
        piece.piece_type == chess.QUEEN for piece in board.piece_map().values()
    )
    for color in (chess.WHITE, chess.BLACK):
        king = board.king(color)
        if king is None:
            continue
        side = "белых" if color == chess.WHITE else "чёрных"
        file_index = chess.square_file(king)
        rank = chess.square_rank(king)
        home_rank = 0 if color == chess.WHITE else 7

        if queens_on_board and rank == home_rank and file_index in (3, 4):
            motifs.append(
                Motif(
                    type="king_safety",
                    description=(
                        f"Король {side} в центре ({chess.square_name(king)}), "
                        "ферзи на доске"
                    ),
                    importance=4,
                )
            )
            continue

        # Рокировка в короткую сторону, но пешка f ушла.
        castled_kingside = (color == chess.WHITE and king == chess.G1) or (
            color == chess.BLACK and king == chess.G8
        )
        if castled_kingside:
            f_square = chess.F2 if color == chess.WHITE else chess.F7
            pawn = board.piece_at(f_square)
            if pawn is None or pawn.piece_type != chess.PAWN or pawn.color != color:
                motifs.append(
                    Motif(
                        type="king_safety",
                        description=f"У короля {side} нет пешки f — ослаблен фланг",
                        importance=3,
                    )
                )
    return motifs


# --- тактика по доске -----------------------------------------------------

def _find_fork(board: chess.Board) -> Motif | None:
    color = board.turn
    side = "белых" if color == chess.WHITE else "чёрных"
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type in (chess.PAWN, chess.KING):
            continue
        targets = []
        for attacked in board.attacks(square):
            target = board.piece_at(attacked)
            if target is None or target.color == color:
                continue
            if target.piece_type == chess.KING or _VALUE[target.piece_type] >= VALUABLE_TARGET:
                targets.append(chess.square_name(attacked))
        if len(targets) >= 2:
            names = ", ".join(sorted(targets))
            return Motif(
                type="fork",
                description=(
                    f"Вилка {chess.square_name(square)} у {side}: под ударом {names}"
                ),
                importance=4,
            )
    return None


def _find_pin(board: chess.Board) -> Motif | None:
    enemy = not board.turn
    side = "белых" if enemy == chess.WHITE else "чёрных"
    for square, piece in board.piece_map().items():
        if piece.color != enemy or piece.piece_type == chess.KING:
            continue
        if board.is_pinned(enemy, square):
            return Motif(
                type="pin",
                description=(
                    f"Связка: {piece.symbol().upper()} "
                    f"{chess.square_name(square)} у {side} связана"
                ),
                importance=3,
            )
    return None


def _tactical(board: chess.Board) -> list[Motif]:
    motifs: list[Motif] = []
    fork = _find_fork(board)
    if fork is not None:
        motifs.append(fork)
    pin = _find_pin(board)
    if pin is not None:
        motifs.append(pin)
    return motifs


# --- тактика по линиям движка --------------------------------------------

def _engine_motifs(board: chess.Board, multipv: Sequence[EngineLine]) -> list[Motif]:
    if not multipv:
        return []
    top = multipv[0]
    motifs: list[Motif] = []

    if top.mate is not None and top.mate > 0:
        side = "белых" if board.turn == chess.WHITE else "чёрных"
        motifs.append(
            Motif(
                type="mate_threat",
                description=f"Мат в {top.mate} у {side}",
                importance=5,
            )
        )

    try:
        move = board.parse_uci(top.move_uci)
    except ValueError:
        return motifs
    if not board.is_capture(move):
        return motifs

    captured = board.piece_at(move.to_square)
    mover = board.piece_at(move.from_square)
    if captured is None or mover is None:
        return motifs
    gain = _VALUE[captured.piece_type] - _VALUE[mover.piece_type]
    if gain < 200:
        return motifs
    if board.is_attacked_by(not board.turn, move.to_square):
        return motifs
    motifs.append(
        Motif(
            type="winning_capture",
            description=(
                f"Выигрыш материала: взятие на {chess.square_name(move.to_square)}"
            ),
            importance=3,
        )
    )
    return motifs
