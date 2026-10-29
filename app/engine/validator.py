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

T-030b: публичный вход :func:`validate_moves` (его же вызывает
:func:`validate_llm_response`) обёрнут в OTel-спан ``validator.validate``;
атрибуты — только счётчики/флаг валидности и `trace.id`, без текста LLM.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import chess
from opentelemetry.trace import Span, Status, StatusCode

from app.engine.service import EngineService
from app.ingestion.parser import parse_fen
from app.observability import get_tracer
from app.tracing import trace_id_for_span

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


def _request_trace_id() -> str | None:
    """`trace_id` для атрибута спана (T-030b): UUID запроса или hex OTel."""
    return trace_id_for_span()


def _set_trace_id(span: Span) -> None:
    """Проставить `trace.id` в спан, если удалось определить (без PII)."""
    trace_id = _request_trace_id()
    if trace_id:
        span.set_attribute("trace.id", trace_id)


def validate_moves(
    moves: Sequence[str],
    initial_fen: str,
    *,
    mainline_san: Sequence[str] | None = None,
    extra_fens: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Проверить ходы на доске.

    OTel-спан ``validator.validate`` (T-030b): число проверенных ходов,
    результат, количество нелегальных и `trace.id`. Текст ответа LLM/FEN в
    атрибуты не попадают (без PII).

    Args:
        moves: SAN-ходы из текста (в порядке упоминания).
        initial_fen: стартовая позиция партии.
        mainline_san: ходы партии (SAN). Позволяет считать легальными
            альтернативы, упомянутые для других позиций партии.
        extra_fens: дополнительные позиции (например, из PV-линий движка):
            ход, легальный в них, считается корректным — модель имеет право
            цитировать вариант из пакета фактов.

    Returns:
        ``(все_легальны, список_нелегальных)`` — нелегальные без повторов.

    Raises:
        InvalidFenError: `initial_fen` или один из `extra_fens` некорректен.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("validator.validate") as span:
        _set_trace_id(span)
        span.set_attribute("validator.moves", len(moves))
        if mainline_san is not None:
            span.set_attribute("validator.mainline_moves", len(mainline_san))
        if extra_fens is not None:
            span.set_attribute("validator.extra_fens", len(extra_fens))
        try:
            result = _validate_moves_impl(
                moves,
                initial_fen,
                mainline_san=mainline_san,
                extra_fens=extra_fens,
            )
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            raise
        legal, illegal = result
        span.set_attribute("validator.valid", legal)
        span.set_attribute("validator.illegal_count", len(illegal))
        span.set_status(Status(StatusCode.OK))
        return result


def _validate_moves_impl(
    moves: Sequence[str],
    initial_fen: str,
    *,
    mainline_san: Sequence[str] | None = None,
    extra_fens: Sequence[str] | None = None,
) -> tuple[bool, list[str]]:
    """Тело :func:`validate_moves` без OTel-обвязки."""
    start = parse_fen(initial_fen)
    known_positions = _mainline_positions(start, mainline_san)
    for fen in extra_fens or ():
        known_positions.append(parse_fen(fen))

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
        if _is_bare_square(san):
            # Одиночная клетка (`e1`, `a4`) неоднозначна: это может быть
            # название поля («король на e1»), а не ход. Если она нелегальна
            # во всех позициях партии, считаем её упоминанием поля, иначе
            # валидатор массово отбраковывает нормальные комментарии.
            continue
        if san not in illegal:
            illegal.append(san)

    return (not illegal, illegal)


async def validate_llm_response(
    text: str,
    initial_fen: str,
    engine_service: EngineService | None = None,
    *,
    mainline_san: Sequence[str] | None = None,
    extra_fens: Sequence[str] | None = None,
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
    # Подпись из постановки; зарезервирован под strict-проверку (T-016).
    return validate_moves(
        extract_moves_from_text(text),
        initial_fen,
        mainline_san=mainline_san,
        extra_fens=extra_fens,
    )


def _is_bare_square(san: str) -> bool:
    """``True`` для одиночной клетки без фигуры/взятия/превращения."""
    return len(san) == 2 and san[0] in "abcdefgh" and san[1] in "12345678"


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
