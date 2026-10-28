"""Валидатор ответов LLM (T-015).

Идея: LLM пишет текст, но **ходы** в нём проверяются на реальной доске.
Нелегальный ход — сигнал, что модель «придумала» вариант; такой ответ нельзя
показывать пользователю (SPEC §2.6).

Проверка двухступенчатая:

1. из текста регулярным выражением извлекаются SAN-ходы
   (`extract_moves_from_text`);
2. ходы проигрываются на доске от `initial_fen`. Если ход не подходит в
   текущей позиции, он всё ещё может быть легальной **альтернативой** в
   другой позиции партии (комментарий часто пишет «лучше было 3.Bb5»);
   для этого передаётся `mainline_san` — список ходов партии, по которому
   строятся все позиции. Ход, нелегальный нигде, попадает в список
   нарушений.

`engine_service` в подписи оставлен для strict-режима (T-016): проверить,
что оценка упомянутого варианта действительно соответствует движку. В
T-015 он не используется — валидация чисто шахматная.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import chess

from app.engine.service import EngineService
from app.ingestion.parser import parse_fen

__all__ = [
    "SAN_PATTERN",
    "extract_moves_from_text",
    "validate_llm_response",
    "validate_moves",
]

#: SAN-ход: фигура/пешка, взятие, превращение, шах/мат, рокировка.
#: Границы `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` не дают ловить ходы внутри слов.
SAN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"O-O-O|O-O|0-0-0|0-0"
    r"|[KQRBN][a-h]?[1-8]?x?[a-h][1-8](?:=[QRBN])?[+#]?"
    r"|[a-h](?:x[a-h])?[1-8](?:=[QRBN])?[+#]?"
    r")"
    r"(?![A-Za-z0-9])"
)


def extract_moves_from_text(text: str) -> list[str]:
    """Извлечь SAN-ходы из текста в порядке упоминания.

    Рокировка в «нулевой» нотации (`0-0`) нормализуется в `O-O`.
    Markdown вокруг хода (`**e4**`, `e4`) не мешает: звёздочки — не буквы.
    """
    moves: list[str] = []
    for match in SAN_PATTERN.finditer(text):
        moves.append(match.group(0).replace("0", "O"))
    return moves


def validate_moves(
    moves: Sequence[str],
    initial_fen: str,
    *,
    mainline_san: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Проверить ходы на доске.

    Args:
        moves: SAN-ходы из текста (в порядке упоминания).
        initial_fen: стартовая позиция партии.
        mainline_san: ходы партии (SAN). Позволяет считать легальными
            альтернативы, упомянутые для других позиций партии.

    Returns:
        ``(все_легальны, список_нелегальных)`` — нелегальные без повторов.

    Raises:
        InvalidFenError: `initial_fen` некорректен.
    """
    start = parse_fen(initial_fen)
    known_positions = _mainline_positions(start, mainline_san)

    cursor = chess.Board(start.fen())
    illegal: list[str] = []
    for san in moves:
        move = _try_parse(cursor, san)
        if move is not None:
            cursor.push(move)
            continue
        if any(_try_parse(position, san) is not None for position in known_positions):
            # Альтернатива к одной из позиций партии — допустимо.
            continue
        if san not in illegal:
            illegal.append(san)

    return (not illegal, illegal)


async def validate_llm_response(
    text: str,
    initial_fen: str,
    engine_service: EngineService,
    *,
    mainline_san: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Извлечь ходы из ответа LLM и проверить их легальность.

    Args:
        text: ответ модели.
        initial_fen: стартовая позиция партии.
        engine_service: зарезервирован под strict-проверку оценок (T-016);
            в T-015 не используется.
        mainline_san: ходы партии для проверки альтернатив.

    Returns:
        ``(все_легальны, список_нелегальных)``.
    """
    del engine_service  # подпись из постановки; см. docstring модуля
    return validate_moves(
        extract_moves_from_text(text), initial_fen, mainline_san=mainline_san
    )


def _try_parse(board: chess.Board, san: str) -> chess.Move | None:
    try:
        return board.parse_san(san)
    except ValueError:
        return None


def _mainline_positions(
    start: chess.Board, mainline_san: Sequence[str] | None
) -> list[chess.Board]:
    """Позиции партии: стартовая + после каждого легального хода mainline."""
    positions = [start.copy()]
    if not mainline_san:
        return positions
    board = chess.Board(start.fen())
    for san in mainline_san:
        move = _try_parse(board, san)
        if move is None:
            break
        board.push(move)
        positions.append(board.copy())
    return positions
